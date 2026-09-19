from pathlib import Path
import ssl


def tls_context():
    """Retain certificate verification and include the OS CA bundle when present."""
    context = ssl.create_default_context()
    system_bundle = Path("/etc/ssl/cert.pem")
    if system_bundle.is_file():
        context.load_verify_locations(cafile=str(system_bundle))
    return context
