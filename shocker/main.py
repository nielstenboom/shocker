import click
from pathlib import Path
from shocker.docker_registry import DockerRegistryClient
from shocker.run import run_container

@click.group()
def cli():
    """Shocker - A simple Docker-like container runner."""
    pass

@cli.command()
@click.argument('repository')
@click.option('--architecture', default='amd64', help='Target architecture (default: amd64)')
@click.option('--os-type', default='linux', help='Target OS (default: linux)')
def pull(repository: str, architecture: str, os_type: str):
    """Pull a Docker image from the registry."""
    tag = 'latest'
    if ':' in repository:
        repository, tag = repository.split(':', 1)

    client = DockerRegistryClient(repository=repository, tag=tag)

    client.pull(
        architecture=architecture,
        os_type=os_type
    )
    click.echo(f"✅ Successfully pulled {repository}:{tag}")


@cli.command()
def list():
    """List all pulled images."""
    images = DockerRegistryClient.list()
    
    if not images:
        click.echo("No images found.")
        return
    
    click.echo("\nPulled Images:")
    click.echo("-" * 50)
    for img in images:
        click.echo(f"{img['repository']}:{img['tag']}")
        click.echo(f"  Path: {img['path']}")
        click.echo()

@cli.command()
@click.argument('image')
@click.argument('command', nargs=-1)
def run(image, command):
    """Run a command in a container."""
    if ':' in image:
        repository, tag = image.split(':', 1)
    else:
        repository, tag = image, 'latest'
    
    cmd = list(command) if command else ['/bin/sh']

    click.echo(f"🚀 Running {repository}:{tag} with command: {' '.join(cmd)}")
    
    run_container(repository, tag, cmd)

if __name__ == "__main__":
    cli()
