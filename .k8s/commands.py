#!/usr/bin/python
import click
from jinja2 import Template
import utils
from pathlib import Path
import shutil
from time import sleep
import yaml
from kubernetes.client.rest import ApiException
import kubernetes

# Variables
templatesLocation = '.k8s/templates'
generatedLocation = '.k8s/generated'

with open(f'{templatesLocation}/default.yaml.tmpl') as file_:
    defaultManifestTemplate = Template(file_.read())

with open(f'{templatesLocation}/manifest.yaml.tmpl') as file_:
    manifestTemplate = Template(file_.read())

with open(f'{templatesLocation}/framework.profile.yaml.tmpl') as file_:
    frameworkTemplate = Template(file_.read())

with open(f'{templatesLocation}/python.profile.yaml.tmpl') as file_:
    pythonTemplate = Template(file_.read())

with open(f'{templatesLocation}/skaffold.yaml.tmpl') as file_:
    skaffoldTemplate = Template(file_.read())


@click.command('generate', short_help='Generate the Skaffold context')
@click.option('--default', '-d', show_default=True, default="python-3.10", help="Default python version")
@click.option('--exclude', '-e', show_default=True, default=".ci/.jenkins_exclude.yml", help="YAML file with the list of version/framework tuples that are excluded")
@click.option('--framework', '-f', show_default=True, default=".ci/.jenkins_framework.yml", help="YAML file with the list of frameworks")
@click.option('--version', '-v', show_default=True, default=".ci/.jenkins_python.yml", help="YAML file with the list of versions")
def generate(default, version, framework, exclude):
    """Generate the Skaffold files for the given python and frameworks."""
    click.echo(click.style(f"generate(exclude={exclude} framework={framework} version={version})", fg='blue'))
    # Read files
    with open(version, "r") as fp:
        versionFile = yaml.safe_load(fp)
    with open(framework, "r") as fp:
        frameworkFile = yaml.safe_load(fp)
    with open(exclude, "r") as fp:
        excludeFile = yaml.safe_load(fp)

    # Generate the generated folder
    Path(generatedLocation).mkdir(parents=True, exist_ok=False)

    click.echo(click.style("Generating kubernetes configuration on the fly...", fg='yellow'))

    # Generate profiles for the given python versions
    for ver in versionFile.get('PYTHON_VERSION'):
        generateVersionProfiles(ver)

    # Generate profiles for the given python and framewok versions
    for ver in versionFile.get('PYTHON_VERSION'):
        for fra in frameworkFile.get('FRAMEWORK'):
            if not utils.isExcluded(ver, fra, excludeFile):
                generateSkaffoldEntries(ver, fra)

    click.echo(click.style("Generating skaffold configuration on the fly...", fg='yellow'))

    # Generate skaffold with the default python version
    output = skaffoldTemplate.render(version=utils.getPythonVersion(default))
    profilesFile = f'{generatedLocation}/skaffold.yaml.tmp'
    with open(profilesFile, 'w') as f:
        f.write(output)

    # Aggregate all the skaffold files and converge
    filenames = [f'{generatedLocation}/skaffold.yaml.tmp', f'{generatedLocation}/profiles.tmp']
    with open('skaffold.yaml', 'w') as outfile:
        for fname in filenames:
            with open(fname) as infile:
                for line in infile:
                    outfile.write(line)

    click.echo(click.style("Copying dockerignore file...", fg='yellow'))
    # avoid exposing anything unrelated to the source code.
    shutil.copyfile('.k8s/.dockerignore', '.dockerignore')

    click.echo(click.style("Copying default yaml file...", fg='yellow'))
    # skaffold requires a default manifest ... this is the workaround for now.
    generateDefaultManifest(default)


@click.command('build', short_help='Build the docker images')
@click.option('--version', '-v', multiple=True, help="Python version to be built")
@click.option('--repo', '-r', show_default=True, default="docker.elastic.co/beats-dev", help="Docker repository")
@click.option('--extra', '-x', help="Extra arguments for the skaffold tool.")
def build(version, repo, extra):
    """Build docker images that contain your workspace and publish them to the given Docker repository."""
    # Enable the skaffold profiles matching the given version, if any
    profilesFlag = ''
    if version:
        profilesFlag = '-p ' +','.join(version)
    defaultRepositoryFlag = ''
    if repo:
        defaultRepositoryFlag = f'--default-repo={repo}'
    extraFlag = ''
    if extra:
        extraFlag = f'{extra}'
    command = f'skaffold build {extraFlag} {defaultRepositoryFlag} --file-output={generatedLocation}/tags.json {profilesFlag}'
    utils.runCommand(command)


@click.command('test', short_help='Test support matrix')
@click.option('--framework', '-f', multiple=True, help="Framework to be tested.")
@click.option('--version', '-v', multiple=True, help="Python version to be tested.")
@click.option('--extra', '-x', help="Extra arguments for the skaffold tool.")
@click.option('--namespace', '-n', show_default=True, default="default", help="Run the in the specified namespace")
def test(framework, version, extra, namespace):
    """Run the test support matrix for the default version and frameworks or filtered by them."""
    click.echo(click.style(f"framework={framework} version={version}", fg='blue'))
    ## TODO set the --label=user.repo=git-username
    #deploy(framework, version, extra, namespace)
    results(framework, version, namespace)


def deploy(framework, version, extra, namespace):
    """Given the python and framework then run the skaffold deployment"""
    # Enable the skaffold profiles matching the given framework and version, if any
    profilesFlag = ''
    if framework or version:
        profilesFlag = '-p ' + ','.join(framework + version)
    extraFlag = ''
    if extra:
        extraFlag = f'{extra}'
    command = f'skaffold deploy {extraFlag} --build-artifacts={generatedLocation}/tags.json -n {namespace} {profilesFlag}'
    utils.runCommand(command)


def results(framework, version, namespace):
    """Given the python and framework then gather the results when the jobs have finished"""
    click.echo(click.style(f"TBC framework={framework} version={version}", fg='blue'))

    ## Loop for each version/framework and activiley look for whether it has finished and if so the
    #for ver in version:
    #    for fram in framework:
    # Configs can be set in Configuration class directly or using helper
    # utility. If no argument provided, the config will be loaded from
    # default location.
    kubernetes.config.load_kube_config()
    with kubernetes.client.ApiClient() as api_client:
        api_instance = kubernetes.client.BatchV1Api(api_client)
        try:
            label_selector = f'repo=apm-agent-python,type=unit-test'
            result = api_instance.list_namespaced_job(namespace, label_selector=label_selector)
            #print(api_response)
            print(len(result.items))
            for r in result.items:
                print(r.metadata.name)
                get_job_status(api_instance, r.metadata.name, namespace)
        except ApiException as e:
            print("Exception when calling BatchV1Api->list_namespaced_job: %s\n" % e)


def get_job_status(api_instance, job_name, namespace):
    job_completed = False
    while not job_completed:
        api_response = api_instance.read_namespaced_job_status(
            name=job_name,
            namespace=namespace)
        if api_response.status.succeeded is not None or \
                api_response.status.failed is not None:
            job_completed = True
        sleep(1)
        print('.', end=" ")
        #print("Job status='%s'" % str(api_response.status))
    print("Job '%s' finished" % str(job_name))


def generateSkaffoldEntries(version, framework):
    """Given the python and framework then generate the k8s manifest and skaffold profile"""
    # print(" - generating skaffold for " + version + " and " + framework)
    pythonVersion = utils.getPythonVersion(version)
    frameworkName = utils.getFrameworkName(framework)

    # Render the template
    output = manifestTemplate.render(pythonVersion=pythonVersion,framework=framework)

    # Generate the opinionated folder structure
    skaffoldDir = f'{generatedLocation}/{pythonVersion}/{frameworkName}'
    Path(skaffoldDir).mkdir(parents=True, exist_ok=True)

    # Generate k8s manifest for the given python version and framework
    skaffoldFile = f'{skaffoldDir}/{pythonVersion}-{framework}.yaml'
    with open(skaffoldFile, 'w') as f:
        f.write(output)

    generateFrameworkProfiles(framework)

def generateDefaultManifest(version):
    """Given the python then generate the default manifest"""
    output = defaultManifestTemplate.render(name=version, version=utils.getPythonVersion(version))

    profilesFile = f'{generatedLocation}/default.yaml'
    with open(profilesFile, 'w') as f:
        f.write(output)

def generateVersionProfiles(version):
    """Given the python then update the generated skaffold profiles for that version"""
    pythonVersion = utils.getPythonVersion(version)
    # Render the template
    output = pythonTemplate.render(name=version, version=pythonVersion)

    profilesFile = f'{generatedLocation}/profiles.tmp'
    with open(profilesFile, 'a') as f:
        f.write(output)

def generateFrameworkProfiles(framework):
    """Given the framework then update the generated skaffold profiles for that framework"""
    name = utils.getFrameworkName(framework)
    version = utils.getFrameworkVersion(framework)
    # Render the template
    output = frameworkTemplate.render(framework=framework, name=name, version=version)

    profilesFile = f'{generatedLocation}/profiles.tmp'
    with open(profilesFile, 'a') as f:
        f.write(output)
