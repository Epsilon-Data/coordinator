"""
Tests for workers/executor/services.py - CryptoService
"""
import pytest
import base64

from workers.executor.services import CryptoService


class TestCryptoService:
    """Test cases for CryptoService class."""

    @pytest.fixture
    def crypto_service(self):
        """Create CryptoService instance."""
        return CryptoService()

    def test_generate_keypair(self, crypto_service):
        """Test generate_keypair returns valid RSA keypair."""
        private_key, public_key_pem = crypto_service.generate_keypair()

        # Check private key exists
        assert private_key is not None
        assert private_key.key_size == 2048

        # Check public key PEM format
        assert public_key_pem.startswith('-----BEGIN PUBLIC KEY-----')
        assert public_key_pem.endswith('-----END PUBLIC KEY-----\n')

    def test_encrypt_returns_base64(self, crypto_service):
        """Test encrypt returns base64-encoded string."""
        _, public_key_pem = crypto_service.generate_keypair()

        data = b"Hello, World!"
        encrypted = crypto_service.encrypt(data, public_key_pem)

        # Should be valid base64
        decoded = base64.b64decode(encrypted)
        assert len(decoded) > len(data)  # Encrypted data is larger

    def test_encrypt_decrypt_roundtrip(self, crypto_service):
        """Test that encrypted data can be decrypted correctly."""
        private_key, public_key_pem = crypto_service.generate_keypair()

        original_data = b"Test data for encryption roundtrip!"
        encrypted = crypto_service.encrypt(original_data, public_key_pem)
        decrypted = crypto_service.decrypt(encrypted, private_key)

        assert decrypted == original_data

    def test_encrypt_decrypt_large_data(self, crypto_service):
        """Test encryption of larger data (beyond single RSA block)."""
        private_key, public_key_pem = crypto_service.generate_keypair()

        # 10KB of data
        original_data = b"A" * 10240
        encrypted = crypto_service.encrypt(original_data, public_key_pem)
        decrypted = crypto_service.decrypt(encrypted, private_key)

        assert decrypted == original_data
        assert len(decrypted) == 10240

    def test_encrypt_decrypt_binary_data(self, crypto_service):
        """Test encryption of binary data (non-text)."""
        private_key, public_key_pem = crypto_service.generate_keypair()

        # Binary data with all byte values
        original_data = bytes(range(256)) * 10
        encrypted = crypto_service.encrypt(original_data, public_key_pem)
        decrypted = crypto_service.decrypt(encrypted, private_key)

        assert decrypted == original_data

    def test_encrypt_decrypt_empty_data(self, crypto_service):
        """Test encryption of empty data."""
        private_key, public_key_pem = crypto_service.generate_keypair()

        original_data = b""
        encrypted = crypto_service.encrypt(original_data, public_key_pem)
        decrypted = crypto_service.decrypt(encrypted, private_key)

        assert decrypted == original_data

    def test_encrypt_with_different_keys_produces_different_output(self, crypto_service):
        """Test that different keys produce different encrypted output."""
        _, public_key1 = crypto_service.generate_keypair()
        _, public_key2 = crypto_service.generate_keypair()

        data = b"Same data"
        encrypted1 = crypto_service.encrypt(data, public_key1)
        encrypted2 = crypto_service.encrypt(data, public_key2)

        # Different keys should produce different output
        assert encrypted1 != encrypted2

    def test_decrypt_with_wrong_key_fails(self, crypto_service):
        """Test that decryption with wrong key raises error."""
        private_key1, public_key1 = crypto_service.generate_keypair()
        private_key2, _ = crypto_service.generate_keypair()

        data = b"Secret data"
        encrypted = crypto_service.encrypt(data, public_key1)

        # Should raise an error when decrypting with wrong key
        with pytest.raises(Exception):  # ValueError or cryptography exception
            crypto_service.decrypt(encrypted, private_key2)

    def test_invalid_public_key_format(self, crypto_service):
        """Test that invalid public key raises error."""
        with pytest.raises(Exception):
            crypto_service.encrypt(b"data", "not-a-valid-key")

    def test_invalid_encrypted_data(self, crypto_service):
        """Test that invalid encrypted data raises error."""
        private_key, _ = crypto_service.generate_keypair()

        with pytest.raises(Exception):
            crypto_service.decrypt("not-valid-base64!", private_key)

    def test_encrypted_structure(self, crypto_service):
        """Test that encrypted data has correct structure."""
        private_key, public_key_pem = crypto_service.generate_keypair()

        data = b"Test"
        encrypted = crypto_service.encrypt(data, public_key_pem)
        combined = base64.b64decode(encrypted)

        # Structure: encrypted_key (256 bytes for 2048-bit RSA) + iv (16 bytes) + ciphertext
        rsa_key_bytes = 256  # 2048 / 8
        iv_size = 16

        # Minimum size: encrypted_key + iv + at least 16 bytes (one AES block)
        assert len(combined) >= rsa_key_bytes + iv_size + 16

    def test_different_encryptions_produce_different_output(self, crypto_service):
        """Test that encrypting same data twice produces different output (due to random IV)."""
        _, public_key_pem = crypto_service.generate_keypair()

        data = b"Same data"
        encrypted1 = crypto_service.encrypt(data, public_key_pem)
        encrypted2 = crypto_service.encrypt(data, public_key_pem)

        # Should be different due to random IV and AES key
        assert encrypted1 != encrypted2
