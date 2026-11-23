import os
import tempfile
from pathlib import Path

test_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(test_directory.name) / "suite.db").as_posix()
os.environ["JWT_SECRET"] = "isolated-test-session"
os.environ.pop("BETO_MODEL_PATH", None)
