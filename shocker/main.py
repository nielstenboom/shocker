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
    click.echo("-" * 70)
    for img in images:
        click.echo(f"{img['repository']}:{img['tag']} ({img['size_mb']} MB)")
        click.echo(f"  Path: {img['path']}")
        click.echo()

@cli.command()
@click.argument('image')
@click.argument('command', nargs=-1)
@click.option('-p', '--port', multiple=True, help='Port forwarding (host:container)')
def run(image, command, port):
    """Run a container with optional port forwarding."""
    if ':' in image:
        repository, tag = image.split(':', 1)
    else:
        repository, tag = image, 'latest'
    
    # Parse port forwarding
    port_mappings = []
    for p in port:
        if ':' in p:
            host_port, container_port = p.split(':', 1)
            port_mappings.append((int(host_port), int(container_port)))
        else:
            port_mappings.append((int(p), int(p)))  # Same port on both sides

    run_container(repository, tag, command, port_mappings=port_mappings)


if __name__ == "__main__":
    cli()