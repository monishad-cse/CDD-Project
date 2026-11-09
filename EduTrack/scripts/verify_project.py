"""Offline structural checks for the EduTrack project package."""
import json
from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]

for relative in ("app.py", "backend/main.py"):
    py_compile.compile(str(ROOT / relative), doraise=True)

with (ROOT / "frontend/package.json").open(encoding="utf-8") as handle:
    package = json.load(handle)
assert package["scripts"].get("build") == "vite build"

try:
    import yaml
except ImportError:
    print("Python syntax and package checks passed. Install PyYAML to validate Compose YAML.")
else:
    with (ROOT / "docker-compose.yml").open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)
    assert {"frontend", "backend", "mysql"}.issubset(compose["services"])

assert (ROOT / "frontend/Dockerfile").is_file()
assert (ROOT / "backend/Dockerfile").is_file()
assert (ROOT / ".github/workflows/ci.yml").is_file()
api_source = (ROOT / "backend/main.py").read_text(encoding="utf-8")
for route in ("/api/auth/login", "/api/students", "/api/faculty", "/api/subjects", "/api/attendance", "/api/marks", "/api/eligibility", "/api/reports", "/api/internships", "/api/notifications"):
    assert route in api_source, f"Missing API route: {route}"
react_source = (ROOT / "frontend/src/main.jsx").read_text(encoding="utf-8")
for component in ("Dashboard", "Students", "Faculty", "Subjects", "Attendance", "Marks", "Eligibility", "Reports", "Internships", "Notifications"):
    assert f"function {component}" in react_source, f"Missing React module: {component}"
print("EduTrack structural checks passed.")
print("This script does not replace a real Docker build or browser/database test.")
