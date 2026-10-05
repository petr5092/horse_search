import os
import asyncio

# Remove old test db before tests to start fresh
if os.path.exists("horse_dataset.db"):
    try:
        os.remove("horse_dataset.db")
    except Exception:
        pass

from fastapi.testclient import TestClient
from app.main import app
from app.users.dao import UserDAO
from app.users.models import UserRole


def run_tests():
    # with TestClient(app) activates the lifespan context manager (async engine setup & table creation)
    with TestClient(app) as client:
        print("--- 1. Testing Root endpoint ---")
        res = client.get("/")
        assert res.status_code == 200, res.text
        print("[OK] Root response:", res.json())

        print("\n--- 2. Testing Roles Directory ---")
        res = client.get("/auth/roles")
        assert res.status_code == 200
        roles = res.json()
        assert len(roles) == 4
        print(f"[OK] 4 roles loaded from Table 3.1: {[r['title'] for r in roles]}")

        print("\n--- 3. Testing Registration of First User (Becomes Admin) ---")
        res = client.post("/auth/register", json={
            "email": "admin@vim.ru",
            "full_name": "Иванов Алексей Михайлович (Администратор)",
            "password": "adminpassword123"
        })
        assert res.status_code == 201, res.text
        admin_data = res.json()
        assert admin_data["role"] == "admin"
        print(f"[OK] First user registered: {admin_data['full_name']} -> Role: '{admin_data['role']}'")

        print("\n--- 4. Testing Admin Login and Cookie/Token generation ---")
        res = client.post("/auth/login", json={
            "email": "admin@vim.ru",
            "password": "adminpassword123"
        })
        assert res.status_code == 200, res.text
        admin_token = res.json()["access_token"]
        assert "access_token" in client.cookies or admin_token
        print(f"[OK] Admin logged in. Token: {admin_token[:25]}...")

        print("\n--- 5. Testing Registration of Second User (Becomes Annotator) ---")
        res = client.post("/auth/register", json={
            "email": "shibanov@vim.ru",
            "full_name": "Шибанов Петр Геннадиевич",
            "password": "userpassword123"
        })
        assert res.status_code == 201, res.text
        user_data = res.json()
        assert user_data["role"] == "annotator"
        print(f"[OK] Second user registered: {user_data['full_name']} -> Default role: '{user_data['role']}'")

        print("\n--- 6. Testing Duplicate Registration Exception (409 Conflict) ---")
        res = client.post("/auth/register", json={
            "email": "shibanov@vim.ru",
            "full_name": "Дубликат",
            "password": "userpassword123"
        })
        assert res.status_code == 409, f"Expected 409, got {res.status_code}"
        print(f"[OK] UserAlreadyExistsException properly triggered: {res.json()['detail']}")

        print("\n--- 7. Testing Annotator Login and /me endpoint ---")
        with TestClient(app) as user_client:
            res = user_client.post("/auth/login", json={
                "email": "shibanov@vim.ru",
                "password": "userpassword123"
            })
            assert res.status_code == 200

            res = user_client.get("/auth/me")
            assert res.status_code == 200
            me = res.json()
            print(f"[OK] /me endpoint via Cookie: {me['full_name']}, Role: {me['role_info']['title']}, Ограничение: {me['role_info']['restriction']}")

            print("\n--- 8. Testing RBAC: Annotator cannot access Admin-only /all or update role ---")
            res = user_client.get("/auth/all")
            assert res.status_code == 403
            print(f"[OK] ForbiddenException triggered on /auth/all for annotator")

            res = user_client.patch(f"/auth/{user_data['id']}/role", json={"new_role": "reviewer"})
            assert res.status_code == 403
            print(f"[OK] ForbiddenException triggered on role change attempt by non-admin")

        print("\n--- 9. Testing Admin updates Annotator -> Reviewer ---")
        with TestClient(app) as admin_client:
            admin_client.post("/auth/login", json={
                "email": "admin@vim.ru",
                "password": "adminpassword123"
            })

            res = admin_client.patch(f"/auth/{user_data['id']}/role", json={"new_role": "reviewer"})
            assert res.status_code == 200, res.text
            updated = res.json()
            assert updated["role"] == "reviewer"
            print(f"[OK] Admin successfully updated role: {updated['full_name']} is now '{updated['role_info']['title']}'")

            print("\n--- 10. Testing Admin sees all users on /auth/all ---")
            res = admin_client.get("/auth/all")
            assert res.status_code == 200
            all_users = res.json()
            assert len(all_users) == 2
            print(f"[OK] /auth/all retrieved {len(all_users)} users")

    # Direct asynchronous DAO test (as in )
    async def async_dao_test():
        print("\n--- 11. Testing Asynchronous DAO operations (BaseDAO & UserDAO) ---")
        admin = await UserDAO.find_by_id(1)
        assert admin is not None
        assert admin.email == "admin@vim.ru"
        print(f"[OK] UserDAO.find_by_id(1) -> {admin.email}")

        annotator = await UserDAO.find_by_fil(email="shibanov@vim.ru")
        assert annotator is not None
        assert annotator.role == UserRole.REVIEWER
        print(f"[OK] UserDAO.find_by_fil(email=...) (petr5092 method) -> {annotator.email} with role {annotator.role}")

        all_dao_users = await UserDAO.get_all()
        assert len(all_dao_users) == 2
        print(f"[OK] UserDAO.get_all() (petr5092 method) -> {len(all_dao_users)} users found")

        count = await UserDAO.count()
        assert count == 2
        print(f"[OK] UserDAO.count() -> {count} users")

    asyncio.run(async_dao_test())

    print("\n========================================================")
    print("ALL TESTS PASSED! FULLY ASYNCHRONOUS FASTAPI & SQLALCHEMY")
    print("========================================================")


if __name__ == "__main__":
    run_tests()
