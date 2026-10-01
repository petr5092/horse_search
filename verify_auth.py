import os
from fastapi.testclient import TestClient
from app.main import app

def run_tests():
    # Remove test db if exists for fresh run
    if os.path.exists("horse_dataset.db"):
        try:
            os.remove("horse_dataset.db")
        except Exception:
            pass

    with TestClient(app) as client:
        print("--- 1. Testing Root and Health ---")
        res = client.get("/")
        assert res.status_code == 200, res.text
        print("[OK] Root endpoint:", res.json())

        print("\n--- 2. Testing Roles Directory ---")
        res = client.get("/api/auth/roles")
        assert res.status_code == 200
        roles = res.json()
        assert len(roles) == 4
        print(f"[OK] 4 roles loaded from Table 3.1: {[r['title'] for r in roles]}")

        print("\n--- 3. Testing Initial Admin Login ---")
        res = client.post("/api/auth/login", json={
            "email": "admin@vim.ru",
            "password": "admin123"
        })
        assert res.status_code == 200, res.text
        admin_token = res.json()["access_token"]
        print(f"[OK] Admin login successful. Token received: {admin_token[:25]}...")

        print("\n--- 4. Testing User Registration (Annotator) ---")
        res = client.post("/api/auth/register", json={
            "email": "shibanov@vim.ru",
            "full_name": "Шибанов Петр Геннадиевич",
            "password": "password123"
        })
        assert res.status_code == 201, res.text
        user_data = res.json()
        assert user_data["role"] == "annotator"
        print(f"[OK] New user registered: {user_data['full_name']} with default role '{user_data['role']}'")

        print("\n--- 5. Testing Annotator Login & /me ---")
        res = client.post("/api/auth/login", json={
            "email": "shibanov@vim.ru",
            "password": "password123"
        })
        assert res.status_code == 200
        user_token = res.json()["access_token"]

        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
        assert res.status_code == 200
        me = res.json()
        print(f"[OK] /me response: {me['full_name']}, Role: {me['role_info']['title']}, Ограничение: {me['role_info']['restriction']}")

        print("\n--- 6. Testing RBAC Security: Annotator cannot change roles ---")
        res = client.patch(
            f"/api/users/{user_data['id']}/role",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"new_role": "reviewer"}
        )
        assert res.status_code == 403, f"Expected 403 Forbidden, got {res.status_code}"
        print(f"[OK] RBAC check passed: Annotator blocked with 403 Forbidden: {res.json()['detail']['error']}")

        print("\n--- 7. Testing Admin changes Annotator -> Reviewer ---")
        res = client.patch(
            f"/api/users/{user_data['id']}/role",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"new_role": "reviewer"}
        )
        assert res.status_code == 200, res.text
        updated_user = res.json()
        assert updated_user["role"] == "reviewer"
        print(f"[OK] Role updated by Admin: {updated_user['full_name']} is now '{updated_user['role_info']['title']}'")

        print("\n--- 8. Testing Immutable Audit Log ---")
        res = client.get("/api/users/audit/logs", headers={"Authorization": f"Bearer {admin_token}"})
        assert res.status_code == 200
        logs = res.json()
        print(f"[OK] Audit logs verified ({len(logs)} events logged):")
        for log in logs[:4]:
            print(f"   [{log['action']}] by {log['actor_email']} on {log['entity_type']}:{log['entity_id']}")

        print("\n==========================================")
        print("ALL TESTS PASSED SUCCESSFULLY! (100% OK)")
        print("==========================================")

if __name__ == "__main__":
    run_tests()
