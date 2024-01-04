#! /usr/bin/env nix-shell
#! nix-shell --pure -i python3 -p python3Packages.typer python3Packages.pyyaml awscli2 ssm-session-manager-plugin

import json
import subprocess
from dataclasses import dataclass

import typer
import yaml

app = typer.Typer()


@dataclass(kw_only=True)
class Jump:
    name: str  # Name of jump
    target_instance_name: str  # Name of the target to lookup
    remote_host: str  # Remote host to jump to
    remote_port: int  # Port to jump to on remote host
    local_port: int  # Local port to bind to
    aws_profile: str  # AWS profile to use for AWS cli commands
    remote_host_is_a_vpc_endpoint: bool = (
        False  # If true, will look up DNS name to use as remote host in SSM connection
    )


def parse_jumps_from_config() -> dict[str, Jump]:
    try:
        with open("config.yaml", "r") as f:
            raw = yaml.safe_load(f)
            jumps = [Jump(**j) for j in raw["jumps"]]
            return {j.name: j for j in jumps}
    except Exception as e:
        typer.secho(
            f"Failed to parse config.yaml. Please have a look at config.example.yaml for syntax. The error: {e}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


def lookup_instance_id(
    instance_name: str, aws_profile: str, verbose: bool = False
) -> str:
    typer.secho(
        f"Looking up the instance ID of {instance_name}", fg=typer.colors.MAGENTA
    )
    try:
        cmd = f"aws --profile {aws_profile} ec2 describe-instances"
        cmd += f' --query "Reservations[*].Instances[*].[InstanceId]" --output text'
        cmd += f" --filters"
        cmd += f" Name=tag:Name,Values={instance_name}"
        cmd += f" Name=instance-state-name,Values=running"
        cmd += " --output text"
        if verbose:
            typer.secho(cmd, fg=typer.colors.CYAN)

        instance_id = subprocess.check_output(cmd, shell=True).decode().strip()

        if verbose:
            typer.secho(f"{instance_id=}", fg=typer.colors.CYAN)

        if not instance_id:
            typer.secho(
                f"Could not find instance ID for {instance_name}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

        if "\n" in instance_id:
            typer.secho(
                "Found more than one instance. This is not supported at the moment.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

    except subprocess.CalledProcessError:
        typer.secho(
            f"Failed to lookup the instance ID of {instance_name}", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)
    return instance_id


def lookup_dns_from_vpc_endpoint(
    vpc_endpoint_name: str, aws_profile: str, verbose: bool = False
) -> str:
    typer.secho(
        f"Looking up the DNS from VPC endpoint {vpc_endpoint_name}",
        fg=typer.colors.MAGENTA,
    )
    try:
        cmd = f"aws --profile {aws_profile} ec2 describe-vpc-endpoints"
        cmd += f' --query "VpcEndpoints[0].DnsEntries[0].DnsName"'
        cmd += f" --filters"
        cmd += f" Name=tag:Name,Values={vpc_endpoint_name}"
        cmd += " --output text"
        if verbose:
            typer.secho(cmd, fg=typer.colors.CYAN)

        dns_name = subprocess.check_output(cmd, shell=True).decode().strip()

        if verbose:
            typer.secho(f"{dns_name=}", fg=typer.colors.CYAN)

        if not dns_name:
            typer.secho(
                f"Could not find DNS for {vpc_endpoint_name}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)

    except subprocess.CalledProcessError:
        typer.secho(
            f"Failed to lookup the DNS of {vpc_endpoint_name}", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)
    return dns_name


def start_ssm_session(
    target_instance_id: str,
    remote_host: str,
    remote_port: int,
    local_port: int,
    aws_profile: str,
    verbose: bool = False,
) -> None:
    typer.secho(f"Establishing an SSM session", fg=typer.colors.MAGENTA)

    parameters = {
        "host": [str(remote_host)],
        "portNumber": [str(remote_port)],
        "localPortNumber": [str(local_port)],
    }
    cmd = f"aws --profile {aws_profile} "
    cmd += f"ssm start-session --target {target_instance_id} "
    cmd += f"--document-name AWS-StartPortForwardingSessionToRemoteHost --parameters "
    cmd += f"'{json.dumps(parameters)}'"

    try:
        if verbose:
            typer.secho(cmd, fg=typer.colors.CYAN)
        subprocess.run(cmd, shell=True)
    except subprocess.CalledProcessError:
        typer.secho(f"Failed to establish an SSM session", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command(help="Establish a tunnel to a remote host using SSM")
def jump(
    name: str = typer.Argument(
        ...,
        help="Name of the jump (specified in config.yaml)",
    ),
    verbose: bool = typer.Option(
        False,
        help="Enable verbosity",
    ),
):
    jumps = parse_jumps_from_config()
    target = jumps.get(name, None)
    if target is None:
        typer.secho(
            f"Jump {name} not specified in config.yaml",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    target_instance_id = lookup_instance_id(
        target.target_instance_name,
        target.aws_profile,
        verbose=verbose,
    )

    if target.remote_host_is_a_vpc_endpoint:
        target_remote_host = lookup_dns_from_vpc_endpoint(
            vpc_endpoint_name=target.remote_host,
            aws_profile=target.aws_profile,
            verbose=verbose,
        )
    else:
        target_remote_host = target.remote_host

    start_ssm_session(
        target_instance_id,
        target_remote_host,
        target.remote_port,
        target.local_port,
        target.aws_profile,
        verbose=verbose,
    )


if __name__ == "__main__":
    app()
