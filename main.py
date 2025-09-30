from shocker.docker_registry import DockerRegistryClient

def main():
    """Main function demonstrating the Docker registry workflow."""
    repository = "nginx"
    tag = "latest"
    
    client = DockerRegistryClient(repository=repository, tag=tag)
    result = client.pull()

    print(result)

if __name__ == "__main__":
    main()
