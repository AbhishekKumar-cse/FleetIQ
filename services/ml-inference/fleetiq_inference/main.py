"""Bind this private application to loopback or an internal container network."""

from fleetiq_inference.serving import create_app

app = create_app()
