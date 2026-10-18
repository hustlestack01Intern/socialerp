"""
Integration tests for Fake Daraja server.
"""

import base64

import httpx
import pytest


class TestFakeDarajaServer:
    """Tests for Fake Daraja server."""

    @pytest.fixture
    def base_url(self):
        return "http://localhost:8081"

    @pytest.fixture
    def shortcode(self):
        return "174379"

    @pytest.fixture
    def passkey(self):
        return "test_passkey"

    def generate_password(self, shortcode: str, passkey: str, timestamp: str) -> str:
        """Generate Daraja password."""
        data = f"{shortcode}{passkey}{timestamp}"
        return base64.b64encode(data.encode()).decode()

    @pytest.mark.asyncio
    async def test_health_check(self, base_url):
        """Health endpoint returns 200."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_oauth_token(self, base_url):
        """OAuth token endpoint returns token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/oauth/v1/generate",
                headers={
                    "Authorization": "Basic dGVzdF9jb25zdW1lcl9rZXk6dGVzdF9jb25zdW1lcl9zZWNyZXQ=",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert "expires_in" in data

    @pytest.mark.asyncio
    async def test_stk_push_success(self, base_url, shortcode, passkey):
        """STK Push request returns CheckoutRequestID."""
        timestamp = "20240115103000"
        password = self.generate_password(shortcode, passkey, timestamp)

        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": 5000,
            "PartyA": "254712345678",
            "PartyB": shortcode,
            "PhoneNumber": "254712345678",
            "CallBackURL": "https://example.com/callback",
            "AccountReference": "ORDER-123",
            "TransactionDesc": "Order #123 payment",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": "Bearer fake_token"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "CheckoutRequestID" in data
            assert "MerchantRequestID" in data
            assert data["ResponseCode"] == "0"

    @pytest.mark.asyncio
    async def test_stk_push_invalid_password(self, base_url, shortcode):
        """STK Push with invalid password rejected."""
        payload = {
            "BusinessShortCode": shortcode,
            "Password": "invalid_password",
            "Timestamp": "20240115103000",
            "TransactionType": "CustomerPayBillOnline",
            "Amount": 5000,
            "PartyA": "254712345678",
            "PartyB": shortcode,
            "PhoneNumber": "254712345678",
            "CallBackURL": "https://example.com/callback",
            "AccountReference": "ORDER-123",
            "TransactionDesc": "Order #123 payment",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": "Bearer fake_token"},
            )
            assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_simulate_callback_success(self, base_url, shortcode, passkey):
        """Simulate successful callback."""
        # First create an STK push request
        timestamp = "20240115103000"
        password = self.generate_password(shortcode, passkey, timestamp)

        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": 5000,
            "PartyA": "254712345678",
            "PartyB": shortcode,
            "PhoneNumber": "254712345678",
            "CallBackURL": "https://example.com/callback",
            "AccountReference": "ORDER-123",
            "TransactionDesc": "Order #123 payment",
        }

        async with httpx.AsyncClient() as client:
            # Create request
            response = await client.post(
                f"{base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": "Bearer fake_token"},
            )
            checkout_request_id = response.json()["CheckoutRequestID"]

            # Simulate success callback
            response = await client.post(
                f"{base_url}/simulate/callback/{checkout_request_id}",
                params={"result_code": "0", "result_desc": "Success"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "simulated"
            assert "callback_payload" in data
            callback = data["callback_payload"]
            assert callback["Body"]["stkCallback"]["ResultCode"] == "0"
            items = callback["Body"]["stkCallback"]["CallbackMetadata"]["Item"]
            receipt_item = next(i for i in items if i["Name"] == "MpesaReceiptNumber")
            assert receipt_item["Value"].startswith("MPE")

    @pytest.mark.asyncio
    async def test_simulate_callback_failure_modes(self, base_url, shortcode, passkey):
        """Test all failure modes."""
        failure_modes = [
            ("insufficient_funds", "1", "Insufficient funds"),
            ("wrong_pin", "1032", "Wrong PIN"),
            ("cancelled", "1032", "Cancelled by user"),
            ("timeout", "1", "Timeout"),
        ]

        for mode, expected_code, expected_desc in failure_modes:
            timestamp = "20240115103000"
            password = self.generate_password(shortcode, passkey, timestamp)

            payload = {
                "BusinessShortCode": shortcode,
                "Password": password,
                "Timestamp": timestamp,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": 5000,
                "PartyA": "254712345678",
                "PartyB": shortcode,
                "PhoneNumber": "254712345678",
                "CallBackURL": "https://example.com/callback",
                "AccountReference": f"ORDER-{mode}",
                "TransactionDesc": f"Test {mode}",
            }

            async with httpx.AsyncClient() as client:
                # Configure failure mode
                await client.post(
                    f"{base_url}/simulate/failure",
                    json={
                        "tenant_id": shortcode,
                        "phone_number": "254712345678",
                        "failure_mode": mode,
                    },
                )

                # Create request
                response = await client.post(
                    f"{base_url}/mpesa/stkpush/v1/processrequest",
                    json=payload,
                    headers={"Authorization": "Bearer fake_token"},
                )
                checkout_request_id = response.json()["CheckoutRequestID"]

                # Simulate callback (should use failure mode)
                response = await client.post(f"{base_url}/simulate/callback/{checkout_request_id}")
                assert response.status_code == 200
                callback = response.json()["callback_payload"]
                assert callback["Body"]["stkCallback"]["ResultCode"] == expected_code
                assert (
                    expected_desc.lower() in callback["Body"]["stkCallback"]["ResultDesc"].lower()
                )

                # Clear failure mode
                await client.post(
                    f"{base_url}/simulate/failure",
                    json={
                        "tenant_id": shortcode,
                        "phone_number": "254712345678",
                        "failure_mode": "none",
                    },
                )

    @pytest.mark.asyncio
    async def test_query_status(self, base_url, shortcode, passkey):
        """Query STK Push status."""
        timestamp = "20240115103000"
        password = self.generate_password(shortcode, passkey, timestamp)

        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": 5000,
            "PartyA": "254712345678",
            "PartyB": shortcode,
            "PhoneNumber": "254712345678",
            "CallBackURL": "https://example.com/callback",
            "AccountReference": "ORDER-123",
            "TransactionDesc": "Order #123 payment",
        }

        async with httpx.AsyncClient() as client:
            # Create request
            response = await client.post(
                f"{base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": "Bearer fake_token"},
            )
            checkout_request_id = response.json()["CheckoutRequestID"]

            # Query status
            query_payload = {
                "BusinessShortCode": shortcode,
                "Password": password,
                "Timestamp": timestamp,
                "CheckoutRequestID": checkout_request_id,
            }
            response = await client.post(
                f"{base_url}/mpesa/stkpushquery/v1/query",
                json=query_payload,
                headers={"Authorization": "Bearer fake_token"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["CheckoutRequestID"] == checkout_request_id
            assert "ResultCode" in data

    @pytest.mark.asyncio
    async def test_reconciliation(self, base_url, shortcode):
        """Daily reconciliation endpoint."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/reconciliation/daily",
                params={"tenant_id": shortcode, "date": "2024-01-15"},
                json={"transactions": []},
            )
            assert response.status_code == 200
            data = response.json()
            assert "reconciliation_id" in data
            assert "mismatches" in data

    @pytest.mark.asyncio
    async def test_failure_simulation_config(self, base_url, shortcode):
        """Configure and list failure simulations."""
        async with httpx.AsyncClient() as client:
            # Configure failure
            response = await client.post(
                f"{base_url}/simulate/failure",
                json={
                    "tenant_id": shortcode,
                    "phone_number": "254712345678",
                    "failure_mode": "insufficient_funds",
                },
            )
            assert response.status_code == 200

            # List failures
            response = await client.get(
                f"{base_url}/simulate/failure", params={"tenant_id": shortcode},
            )
            assert response.status_code == 200
            failures = response.json()["failures"]
            assert len(failures) >= 1
            assert failures[0]["failure_mode"] == "insufficient_funds"

            # Clear failure
            response = await client.post(
                f"{base_url}/simulate/failure",
                json={
                    "tenant_id": shortcode,
                    "phone_number": "254712345678",
                    "failure_mode": "none",
                },
            )
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_inspect_stk_requests(self, base_url, shortcode):
        """Inspect STK requests."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/inspect/stk_requests", params={"tenant_id": shortcode},
            )
            assert response.status_code == 200
            data = response.json()
            assert "requests" in data

    @pytest.mark.asyncio
    async def test_inspect_callbacks(self, base_url):
        """Inspect callbacks."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/inspect/callbacks")
            assert response.status_code == 200
            data = response.json()
            assert "callbacks" in data

    @pytest.mark.asyncio
    async def test_reset_data(self, base_url, shortcode):
        """Reset test data."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{base_url}/inspect/reset", params={"tenant_id": shortcode},
            )
            assert response.status_code == 200
