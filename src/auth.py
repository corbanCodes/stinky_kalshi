"""
💩 Kalshi API authentication using RSA-PSS signatures.
"""

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend


class KalshiAuth:
    """
    Handles RSA-PSS signature-based authentication for Kalshi API.
    """

    def __init__(self, api_key_id: str, private_key_path: str = None, private_key_base64: str = None):
        self.api_key_id = api_key_id
        self.private_key = self._load_private_key(private_key_path, private_key_base64)

    def _load_private_key(self, path: str = None, base64_key: str = None):
        """Load private key from file or base64 string."""
        if path and Path(path).exists():
            with open(path, "rb") as f:
                key_data = f.read()
        elif base64_key:
            key_data = base64.b64decode(base64_key)
        else:
            raise ValueError("Must provide either private_key_path or private_key_base64")

        return serialization.load_pem_private_key(
            key_data,
            password=None,
            backend=default_backend()
        )

    def get_auth_headers(self, method: str, path: str, body: str = "") -> dict:
        """Generate authentication headers for a request."""
        timestamp = str(int(time.time() * 1000))

        # Create message to sign: timestamp + method + path (NO BODY per Kalshi docs)
        path_without_query = path.split('?')[0]
        message = f"{timestamp}{method.upper()}{path_without_query}"
        message_bytes = message.encode("utf-8")

        # Sign with RSA-PSS
        signature = self.private_key.sign(
            message_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )

        signature_b64 = base64.b64encode(signature).decode("utf-8")

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }
