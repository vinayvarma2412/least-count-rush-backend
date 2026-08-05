"""
Load-test auth bypass patch.
Import this module BEFORE starting uvicorn in load-test mode.

Usage:
  LOAD_TEST_BYPASS_AUTH=1 python -c "import load_test_auth_patch; import uvicorn; uvicorn.run('app.main:app', host='0.0.0.0', port=8000)"

Or source it by adding to run.sh / a separate run_load_test.sh script.
This ONLY patches when LOAD_TEST_BYPASS_AUTH=1 — safe to leave in codebase.
"""
import os
if os.getenv("LOAD_TEST_BYPASS_AUTH", "0") == "1":
    import app.utils.firebase_auth as _fa

    def _mock_verify(id_token: str) -> dict:
        """Accept any token in load-test mode."""
        return {"uid": f"load_test_{id_token[:16]}"}

    _fa.verify_firebase_token = _mock_verify
    print("[load_test_auth_patch] ⚡ Firebase auth BYPASSED for load testing")
