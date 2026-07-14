"""
Runtime MySQL Schema Setup for S13

This script sets up MySQL schema for runtime mode smoke tests.
It does NOT connect to production databases - only local test databases.

Features:
- Connects only to local MySQL (MYSQL_HOST)
- Uses test database only (BCSFUSE_OSS_RUNTIME_SMOKE or MYSQL_DATABASE)
- Creates all required tables for runtime mode
- Supports idempotent create (CREATE TABLE IF NOT EXISTS)
- Supports test cleanup (DROP TABLE IF EXISTS)
- Never prints password

Usage:
    # Setup schema
    python runtime_mysql_schema_setup.py setup

    # Cleanup schema
    python runtime_mysql_schema_setup.py cleanup

    # Check connection
    python runtime_mysql_schema_setup.py check

Environment variables:
    MYSQL_HOST - MySQL host (default: localhost)
    MYSQL_PORT - MySQL port (default: 3306)
    MYSQL_DATABASE - MySQL database (default: bcsfuse_oss_runtime_smoke)
    MYSQL_USER - MySQL user (required)
    MYSQL_PASSWORD - MySQL password (required)
"""
import os
import sys
import logging
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RuntimeMySQLSchemaSetup:
    """Setup MySQL schema for runtime mode."""

    # Schema definitions
    WORKERS_TABLE = """
        CREATE TABLE IF NOT EXISTS workers (
            worker_id VARCHAR(255) PRIMARY KEY,
            identity_name VARCHAR(255),
            identity_handle VARCHAR(255),
            identity_description TEXT,
            worker_type VARCHAR(50),
            state VARCHAR(50),
            availability VARCHAR(50),
            trust_level VARCHAR(50),
            capabilities JSON,
            skills JSON,
            resources JSON,
            metadata JSON,
            domains JSON,
            responsibilities JSON,
            constraints JSON,
            memory_refs JSON,
            version INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_state (state),
            INDEX idx_availability (availability)
        )
    """

    WORKER_RUNTIME_STATE_TABLE = """
        CREATE TABLE IF NOT EXISTS worker_runtime_state (
            worker_id VARCHAR(255) PRIMARY KEY,
            state VARCHAR(50),
            heartbeat_at TIMESTAMP NULL,
            metadata JSON,
            updated_by VARCHAR(255),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """

    WORKER_PROFILE_CONTENT_TABLE = """
        CREATE TABLE IF NOT EXISTS worker_profile_content (
            id INT AUTO_INCREMENT PRIMARY KEY,
            worker_id VARCHAR(255) NOT NULL,
            profile_id VARCHAR(255) NOT NULL,
            content JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_worker_profile (worker_id, profile_id),
            INDEX idx_worker_id (worker_id)
        )
    """

    WORKER_AUDIT_LOG_TABLE = """
        CREATE TABLE IF NOT EXISTS worker_audit_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            worker_id VARCHAR(255) NOT NULL,
            action VARCHAR(100) NOT NULL,
            actor VARCHAR(255),
            details JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_worker_id (worker_id),
            INDEX idx_action (action),
            INDEX idx_created_at (created_at)
        )
    """

    def __init__(self):
        """Initialize schema setup."""
        self.host = os.getenv("MYSQL_HOST", "localhost")
        self.port = int(os.getenv("MYSQL_PORT", "3306"))
        self.database = os.getenv("MYSQL_DATABASE", "bcsfuse_oss_runtime_smoke")
        self.user = os.getenv("MYSQL_USER", "root")
        self.password = os.getenv("MYSQL_PASSWORD", "")

        self._conn = None

    def _connect(self):
        """Connect to MySQL database."""
        try:
            import mysql.connector
            self._conn = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True,
            )
            logger.info(f"Connected to MySQL at {self.host}:{self.port}/{self.database}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MySQL: {e}")
            logger.error(f"  Host: {self.host}")
            logger.error(f"  Port: {self.port}")
            logger.error(f"  Database: {self.database}")
            logger.error(f"  User: {self.user}")
            logger.error("  Password: ***MASKED***")
            return False

    def _close(self):
        """Close MySQL connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def check_connection(self) -> bool:
        """Check if MySQL connection is available.

        Returns:
            True if connection successful, False otherwise.
        """
        logger.info("Checking MySQL connection...")

        if not self._connect():
            logger.error("BLOCKER_LOCAL_MYSQL_NOT_AVAILABLE")
            logger.error("MySQL connection failed. Please ensure:")
            logger.error("  1. MySQL is running locally")
            logger.error(f"  2. Database '{self.database}' exists")
            logger.error(f"  3. User '{self.user}' has access")
            logger.error("  4. MYSQL_PASSWORD is set correctly")
            return False

        logger.info("MySQL connection successful ✓")
        self._close()
        return True

    def setup_schema(self) -> bool:
        """Setup all required tables.

        Returns:
            True if successful, False otherwise.
        """
        logger.info("Setting up MySQL schema...")

        if not self._connect():
            return False

        try:
            cursor = self._conn.cursor()

            # Create tables
            tables = [
                ("workers", self.WORKERS_TABLE),
                ("worker_runtime_state", self.WORKER_RUNTIME_STATE_TABLE),
                ("worker_profile_content", self.WORKER_PROFILE_CONTENT_TABLE),
                ("worker_audit_log", self.WORKER_AUDIT_LOG_TABLE),
            ]

            for table_name, create_sql in tables:
                logger.info(f"Creating table: {table_name}")
                cursor.execute(create_sql)
                logger.info(f"  ✓ Table {table_name} created/verified")

            cursor.close()
            logger.info("MySQL schema setup complete ✓")
            return True

        except Exception as e:
            logger.error(f"Failed to setup schema: {e}")
            return False
        finally:
            self._close()

    def cleanup_schema(self) -> bool:
        """Cleanup all tables (for test cleanup).

        Returns:
            True if successful, False otherwise.
        """
        logger.info("Cleaning up MySQL schema...")

        if not self._connect():
            return False

        try:
            cursor = self._conn.cursor()

            # Drop tables in reverse order (to handle foreign keys if any)
            tables = [
                "worker_audit_log",
                "worker_profile_content",
                "worker_runtime_state",
                "workers",
            ]

            for table_name in tables:
                logger.info(f"Dropping table: {table_name}")
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
                logger.info(f"  ✓ Table {table_name} dropped")

            cursor.close()
            logger.info("MySQL schema cleanup complete ✓")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup schema: {e}")
            return False
        finally:
            self._close()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python runtime_mysql_schema_setup.py [setup|cleanup|check]")
        print()
        print("Commands:")
        print("  setup    - Create all required tables")
        print("  cleanup  - Drop all tables")
        print("  check    - Check MySQL connection")
        print()
        print("Environment variables:")
        print("  MYSQL_HOST      - MySQL host (default: localhost)")
        print("  MYSQL_PORT      - MySQL port (default: 3306)")
        print("  MYSQL_DATABASE  - MySQL database (default: bcsfuse_oss_runtime_smoke)")
        print("  MYSQL_USER      - MySQL user (required)")
        print("  MYSQL_PASSWORD  - MySQL password (required)")
        sys.exit(1)

    command = sys.argv[1]
    setup = RuntimeMySQLSchemaSetup()

    if command == "setup":
        success = setup.setup_schema()
        sys.exit(0 if success else 1)
    elif command == "cleanup":
        success = setup.cleanup_schema()
        sys.exit(0 if success else 1)
    elif command == "check":
        success = setup.check_connection()
        sys.exit(0 if success else 1)
    else:
        print(f"Unknown command: {command}")
        print("Valid commands: setup, cleanup, check")
        sys.exit(1)


if __name__ == "__main__":
    main()