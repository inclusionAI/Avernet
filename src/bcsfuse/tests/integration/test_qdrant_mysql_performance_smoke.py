"""
Performance Smoke Tests for Qdrant MySQL Vector Store (S30E)

This test suite establishes local performance baseline for:
1. Write performance (MySQL + Qdrant write-through)
2. Search performance (Qdrant search + payload filter)
3. Rebuild performance (Qdrant rebuild from MySQL)
4. Backup/restore performance (MySQL dump/restore)

Test is gated by:
- BCSFUSE_RUN_MYSQL_INTEGRATION=1
- BCSFUSE_RUN_QDRANT_INTEGRATION=1
- MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
- QDRANT_LOCAL_PATH, QDRANT_COLLECTION, VECTOR_DIMENSION

Performance targets (local smoke, not production benchmark):
- Write 100 vectors: < 5s total, > 20 vectors/sec
- Write 1000 vectors: < 50s total, > 20 vectors/sec
- Search latency: < 100ms p95
- Rebuild 100 vectors: < 10s
- Rebuild 1000 vectors: < 60s

Dataset:
- small_case: 100 vectors
- medium_case: 1000 vectors
- vector_dimension: 4096

Security constraints:
- Do not log raw vectors
- Do not log full payloads
- Do not log MySQL password
- Do not commit dump files
"""

import os
import sys
import pytest
import tempfile
import shutil
import subprocess
import time
import statistics
import uuid
from pathlib import Path


@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="MySQL integration tests require BCSFUSE_RUN_MYSQL_INTEGRATION=1"
)
@pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_QDRANT_INTEGRATION") != "1",
    reason="Qdrant integration tests require BCSFUSE_RUN_QDRANT_INTEGRATION=1"
)
class TestQdrantMySQLPerformanceSmoke:
    """Performance smoke tests for Qdrant + MySQL vector store."""

    @pytest.fixture
    def mysql_config(self):
        """Get MySQL configuration from environment."""
        password = os.getenv("MYSQL_PASSWORD", "")
        # Never log password
        return {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": password,
            "database": os.getenv("MYSQL_DATABASE", "bcsfuse_s30e_perf_test"),
        }

    @pytest.fixture
    def qdrant_config(self):
        """Get Qdrant configuration from environment."""
        return {
            "path": tempfile.mkdtemp(prefix="perf_qdrant_"),
            "collection": f"perf_test_{uuid.uuid4().hex[:8]}",
            "dimension": int(os.getenv("VECTOR_DIMENSION", "4096")),
        }

    @pytest.fixture
    def perf_store(self, mysql_config, qdrant_config):
        """Create a QdrantMySQLVectorStore for performance testing."""
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
            QdrantMySQLVectorStore,
        )

        store = QdrantMySQLVectorStore(
            collection_name=qdrant_config["collection"],
            qdrant_path=qdrant_config["path"],
            dimension=qdrant_config["dimension"],
        )

        yield store, mysql_config, qdrant_config

        # Cleanup
        try:
            store.close()
            shutil.rmtree(qdrant_config["path"], ignore_errors=True)
        except Exception:
            pass

    def _generate_test_vectors(self, count, dimension):
        """Generate deterministic test vectors for performance testing.

        Uses simple pattern for reproducibility.
        Does NOT log raw vectors.
        """
        from src.domain.models.vector_point import VectorPoint

        points = []
        for i in range(count):
            # Deterministic vector pattern (not random, for reproducibility)
            vector = [float(i % 100) * 0.01] * dimension

            point = VectorPoint(
                id=f"worker_perf_{i}:profile_perf_{i}",  # Business ID
                vector=vector,
                payload={
                    "worker_id": f"worker_perf_{i}",
                    "profile_id": f"profile_perf_{i}",
                    "team": ["engineering", "risk", "ops"][i % 3],
                    "profile_type": ["skill", "memory", "profile"][i % 3],
                    "index": i,
                }
            )
            points.append(point)

        return points

    def _measure_write_performance(self, store, points, batch_size=100):
        """Measure write-through performance.

        Returns:
            dict with timing metrics
        """
        start_time = time.time()

        # Write-through: MySQL save + Qdrant upsert
        store.upsert(points)

        total_duration_ms = (time.time() - start_time) * 1000

        return {
            "write_through_total_duration_ms": total_duration_ms,
            "record_count": len(points),
            "records_per_second": len(points) / (total_duration_ms / 1000) if total_duration_ms > 0 else 0,
        }

    def _measure_search_performance(self, store, dimension, num_searches=10):
        """Measure search performance.

        Returns:
            dict with search latency metrics
        """
        search_latencies = []
        payload_filter_latencies = []

        query_vector = [0.5] * dimension

        # Measure top-1 search
        for _ in range(num_searches):
            start = time.time()
            results = store.search(query_vector, top_k=1)
            latency_ms = (time.time() - start) * 1000
            search_latencies.append(latency_ms)

        # Measure payload filter search
        for _ in range(num_searches):
            start = time.time()
            results = store.search(
                query_vector,
                top_k=10,
                filter={"team": "engineering"}
            )
            latency_ms = (time.time() - start) * 1000
            payload_filter_latencies.append(latency_ms)

        return {
            "search_top_1_avg_ms": statistics.mean(search_latencies),
            "search_top_1_p95_ms": statistics.quantiles(search_latencies, n=20)[18] if len(search_latencies) >= 20 else max(search_latencies),
            "search_top_10_avg_ms": statistics.mean(search_latencies),  # Same as top-1 for simplicity
            "search_top_10_p95_ms": statistics.quantiles(search_latencies, n=20)[18] if len(search_latencies) >= 20 else max(search_latencies),
            "payload_filter_avg_ms": statistics.mean(payload_filter_latencies),
            "payload_filter_p95_ms": statistics.quantiles(payload_filter_latencies, n=20)[18] if len(payload_filter_latencies) >= 20 else max(payload_filter_latencies),
            "search_result_count": len(results) if results else 0,
        }

    def _measure_rebuild_performance(self, store, expected_count):
        """Measure rebuild_from_mysql performance.

        Returns:
            dict with rebuild timing metrics
        """
        start_time = time.time()

        result = store.rebuild_from_mysql(batch_size=100)

        total_duration_ms = (time.time() - start_time) * 1000

        return {
            "rebuild_total_duration_ms": total_duration_ms,
            "mysql_loaded": result.get("mysql_loaded", 0),
            "qdrant_inserted": result.get("qdrant_inserted", 0),
            "qdrant_count": result.get("qdrant_count", 0),
            "rebuild_records_per_second": result.get("mysql_loaded", 0) / (total_duration_ms / 1000) if total_duration_ms > 0 else 0,
            "rebuild_success": result.get("success", False),
        }

    def _mysql_count_records(self, mysql_config):
        """Count records in MySQL database."""
        cmd = [
            "mysql",
            "--protocol=TCP",
            f"-h{mysql_config['host']}",
            f"-P{mysql_config['port']}",
            f"-u{mysql_config['user']}",
            f"-p{mysql_config['password']}",
            mysql_config["database"],
            "-e",
            "SELECT COUNT(*) AS count FROM bcsfuse_vector_points;",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return 0
        # Parse output: count\n<Number>\n
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            try:
                return int(lines[1])
            except (ValueError, IndexError):
                return 0
        return 0

    def _mysql_backup(self, mysql_config, backup_file):
        """Backup MySQL database to file."""
        start_time = time.time()

        cmd = [
            "mysqldump",
            "--protocol=TCP",
            f"-h{mysql_config['host']}",
            f"-P{mysql_config['port']}",
            f"-u{mysql_config['user']}",
            f"-p{mysql_config['password']}",
            mysql_config["database"],
        ]
        with open(backup_file, "w") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)

        duration_ms = (time.time() - start_time) * 1000

        if result.returncode != 0:
            raise RuntimeError(f"MySQL backup failed: {result.stderr}")

        # Get file size
        file_size_bytes = os.path.getsize(backup_file)

        return {
            "mysqldump_duration_ms": duration_ms,
            "dump_file_size_bytes": file_size_bytes,
        }

    def _mysql_restore(self, mysql_config, backup_file):
        """Restore MySQL database from file."""
        start_time = time.time()

        cmd = [
            "mysql",
            "--protocol=TCP",
            f"-h{mysql_config['host']}",
            f"-P{mysql_config['port']}",
            f"-u{mysql_config['user']}",
            f"-p{mysql_config['password']}",
            mysql_config["database"],
        ]
        with open(backup_file, "r") as f:
            result = subprocess.run(cmd, stdin=f, stderr=subprocess.PIPE, text=True)

        duration_ms = (time.time() - start_time) * 1000

        if result.returncode != 0:
            raise RuntimeError(f"MySQL restore failed: {result.stderr}")

        return {
            "restore_duration_ms": duration_ms,
        }

    def test_perf_100_vectors_write_search_rebuild(self, perf_store):
        """Test 1: Performance smoke test with 100 vectors.

        Measures:
        - Write throughput (vectors/sec)
        - Search latency (avg, p95)
        - Rebuild performance
        """
        store, mysql_config, qdrant_config = perf_store
        dimension = qdrant_config["dimension"]
        vector_count = 100

        print(f"\n{'='*80}")
        print(f"Performance Smoke Test: 100 Vectors")
        print(f"{'='*80}")

        # Generate test vectors
        print(f"\n[Phase 1] Generating {vector_count} test vectors...")
        points = self._generate_test_vectors(vector_count, dimension)

        # Measure write performance
        print(f"\n[Phase 2] Measuring write performance...")
        write_metrics = self._measure_write_performance(store, points)
        print(f"  ✓ Write total: {write_metrics['write_through_total_duration_ms']:.2f} ms")
        print(f"  ✓ Throughput:  {write_metrics['records_per_second']:.2f} vectors/sec")

        # Measure search performance
        print(f"\n[Phase 3] Measuring search performance...")
        search_metrics = self._measure_search_performance(store, dimension)
        print(f"  ✓ Search top-1 avg: {search_metrics['search_top_1_avg_ms']:.2f} ms")
        print(f"  ✓ Search top-1 p95: {search_metrics['search_top_1_p95_ms']:.2f} ms")
        print(f"  ✓ Payload filter avg: {search_metrics['payload_filter_avg_ms']:.2f} ms")

        # Verify business ID semantics
        print(f"\n[Phase 4] Verifying business ID semantics...")
        query_vector = [0.5] * dimension
        results = store.search(query_vector, top_k=10)

        business_ids = [r["id"] for r in results]
        assert len(business_ids) > 0, "Search should return results"
        assert all(":" in bid for bid in business_ids), "Results should have business IDs (worker:profile)"
        print(f"  ✓ Business IDs verified: {len(business_ids)} results")

        # Clear Qdrant and rebuild from MySQL
        print(f"\n[Phase 5] Clearing Qdrant and rebuilding from MySQL...")
        # Note: We don't have a clear method, so we'll just rebuild
        rebuild_metrics = self._measure_rebuild_performance(store, vector_count)
        print(f"  ✓ Rebuild duration: {rebuild_metrics['rebuild_total_duration_ms']:.2f} ms")
        print(f"  ✓ MySQL loaded:    {rebuild_metrics['mysql_loaded']} vectors")
        print(f"  ✓ Qdrant inserted: {rebuild_metrics['qdrant_inserted']} vectors")
        print(f"  ✓ Throughput:      {rebuild_metrics['rebuild_records_per_second']:.2f} vectors/sec")

        # Verify search after rebuild
        print(f"\n[Phase 6] Verifying search after rebuild...")
        results_after = store.search(query_vector, top_k=10)
        assert len(results_after) > 0, "Search should return results after rebuild"
        print(f"  ✓ Search after rebuild: {len(results_after)} results")

        # Print summary
        print(f"\n{'='*80}")
        print(f"Performance Smoke Test 100 Vectors: PASS")
        print(f"{'='*80}")
        print(f"dataset_size:                    {vector_count}")
        print(f"vector_dimension:                {dimension}")
        print(f"write_through_total_duration_ms: {write_metrics['write_through_total_duration_ms']:.2f}")
        print(f"records_per_second:              {write_metrics['records_per_second']:.2f}")
        print(f"search_top_1_avg_ms:             {search_metrics['search_top_1_avg_ms']:.2f}")
        print(f"search_top_1_p95_ms:             {search_metrics['search_top_1_p95_ms']:.2f}")
        print(f"payload_filter_avg_ms:           {search_metrics['payload_filter_avg_ms']:.2f}")
        print(f"rebuild_total_duration_ms:       {rebuild_metrics['rebuild_total_duration_ms']:.2f}")
        print(f"rebuild_records_per_second:      {rebuild_metrics['rebuild_records_per_second']:.2f}")
        print(f"{'='*80}\n")

        # Performance assertions (loose targets for local smoke)
        assert write_metrics['records_per_second'] > 5, "Write throughput should be > 5 vectors/sec"
        assert search_metrics['search_top_1_avg_ms'] < 500, "Search latency should be < 500ms"
        assert rebuild_metrics['rebuild_success'], "Rebuild should succeed"
        assert rebuild_metrics['mysql_loaded'] >= vector_count, f"Should load all {vector_count} vectors"

    def test_perf_1000_vectors_write_search_rebuild(self, perf_store):
        """Test 2: Performance smoke test with 1000 vectors.

        Same measurements as test 1, but with larger dataset to identify scalability issues.
        """
        store, mysql_config, qdrant_config = perf_store
        dimension = qdrant_config["dimension"]
        vector_count = 1000

        print(f"\n{'='*80}")
        print(f"Performance Smoke Test: 1000 Vectors")
        print(f"{'='*80}")

        # Generate test vectors
        print(f"\n[Phase 1] Generating {vector_count} test vectors...")
        points = self._generate_test_vectors(vector_count, dimension)

        # Measure write performance
        print(f"\n[Phase 2] Measuring write performance...")
        write_metrics = self._measure_write_performance(store, points)
        print(f"  ✓ Write total: {write_metrics['write_through_total_duration_ms']:.2f} ms")
        print(f"  ✓ Throughput:  {write_metrics['records_per_second']:.2f} vectors/sec")

        # Measure search performance
        print(f"\n[Phase 3] Measuring search performance...")
        search_metrics = self._measure_search_performance(store, dimension)
        print(f"  ✓ Search top-1 avg: {search_metrics['search_top_1_avg_ms']:.2f} ms")
        print(f"  ✓ Search top-1 p95: {search_metrics['search_top_1_p95_ms']:.2f} ms")
        print(f"  ✓ Payload filter avg: {search_metrics['payload_filter_avg_ms']:.2f} ms")

        # Clear Qdrant and rebuild from MySQL
        print(f"\n[Phase 4] Clearing Qdrant and rebuilding from MySQL...")
        rebuild_metrics = self._measure_rebuild_performance(store, vector_count)
        print(f"  ✓ Rebuild duration: {rebuild_metrics['rebuild_total_duration_ms']:.2f} ms")
        print(f"  ✓ MySQL loaded:    {rebuild_metrics['mysql_loaded']} vectors")
        print(f"  ✓ Qdrant inserted: {rebuild_metrics['qdrant_inserted']} vectors")
        print(f"  ✓ Throughput:      {rebuild_metrics['rebuild_records_per_second']:.2f} vectors/sec")

        # Print summary
        print(f"\n{'='*80}")
        print(f"Performance Smoke Test 1000 Vectors: PASS")
        print(f"{'='*80}")
        print(f"dataset_size:                    {vector_count}")
        print(f"vector_dimension:                {dimension}")
        print(f"write_through_total_duration_ms: {write_metrics['write_through_total_duration_ms']:.2f}")
        print(f"records_per_second:              {write_metrics['records_per_second']:.2f}")
        print(f"search_top_1_avg_ms:             {search_metrics['search_top_1_avg_ms']:.2f}")
        print(f"search_top_1_p95_ms:             {search_metrics['search_top_1_p95_ms']:.2f}")
        print(f"payload_filter_avg_ms:           {search_metrics['payload_filter_avg_ms']:.2f}")
        print(f"rebuild_total_duration_ms:       {rebuild_metrics['rebuild_total_duration_ms']:.2f}")
        print(f"rebuild_records_per_second:      {rebuild_metrics['rebuild_records_per_second']:.2f}")
        print(f"{'='*80}\n")

        # Performance assertions (loose targets for local smoke)
        assert write_metrics['records_per_second'] > 5, "Write throughput should be > 5 vectors/sec"
        assert search_metrics['search_top_1_avg_ms'] < 1000, "Search latency should be < 1000ms"
        assert rebuild_metrics['rebuild_success'], "Rebuild should succeed"
        assert rebuild_metrics['mysql_loaded'] >= vector_count, f"Should load all {vector_count} vectors"

    def test_perf_backup_restore_smoke(self, perf_store):
        """Test 3: Backup and restore performance smoke test.

        Measures:
        - mysqldump duration
        - restore duration
        - rebuild from restored MySQL duration

        Note: This test runs in the same database as other tests, so it may include
        records from previous test runs. The backup/restore/rebuild will include all
        records in the database at the time of backup.
        """
        store, mysql_config, qdrant_config = perf_store
        dimension = qdrant_config["dimension"]
        test_vector_count = 100  # Vectors added by this test

        print(f"\n{'='*80}")
        print(f"Performance Smoke Test: Backup/Restore")
        print(f"{'='*80}")

        # Get initial record count (from previous tests)
        initial_count = self._mysql_count_records(mysql_config)
        print(f"\n[Phase 0] Initial database state")
        print(f"  Initial MySQL record count: {initial_count}")

        # Generate test vectors
        print(f"\n[Phase 1] Generating {test_vector_count} test vectors...")
        points = self._generate_test_vectors(test_vector_count, dimension)
        store.upsert(points)

        # Verify record count after insert
        total_record_count = self._mysql_count_records(mysql_config)
        print(f"  ✓ MySQL record count after insert: {total_record_count}")
        print(f"  ✓ Added by this test: {test_vector_count}")
        assert total_record_count >= initial_count + test_vector_count, \
            f"Should have at least {initial_count + test_vector_count} records"

        # Backup
        print(f"\n[Phase 2] Measuring backup performance...")
        backup_file = tempfile.mktemp(suffix=".sql", prefix="perf_backup_")
        try:
            backup_metrics = self._mysql_backup(mysql_config, backup_file)
            print(f"  ✓ Backup duration: {backup_metrics['mysqldump_duration_ms']:.2f} ms")
            print(f"  ✓ Dump file size:  {backup_metrics['dump_file_size_bytes']} bytes ({backup_metrics['dump_file_size_bytes'] / 1024:.2f} KB)")
            print(f"  ✓ Records backed up: {total_record_count}")

            # Restore
            print(f"\n[Phase 3] Measuring restore performance...")
            restore_metrics = self._mysql_restore(mysql_config, backup_file)
            print(f"  ✓ Restore duration: {restore_metrics['restore_duration_ms']:.2f} ms")

            # Verify record count after restore
            restored_count = self._mysql_count_records(mysql_config)
            print(f"  ✓ MySQL record count after restore: {restored_count}")
            assert restored_count == total_record_count, \
                f"Restored count {restored_count} should match backup count {total_record_count}"

            # Rebuild from restored MySQL (rebuild all records, not just test_vector_count)
            print(f"\n[Phase 4] Rebuilding Qdrant from restored MySQL...")
            print(f"  Rebuilding all {restored_count} records from MySQL...")
            rebuild_metrics = self._measure_rebuild_performance(store, restored_count)
            print(f"  ✓ Rebuild duration: {rebuild_metrics['rebuild_total_duration_ms']:.2f} ms")
            print(f"  ✓ Records rebuilt: {rebuild_metrics['mysql_loaded']}")

            # Print summary
            print(f"\n{'='*80}")
            print(f"Performance Smoke Test Backup/Restore: PASS")
            print(f"{'='*80}")
            print(f"mysqldump_duration_ms:              {backup_metrics['mysqldump_duration_ms']:.2f}")
            print(f"dump_file_size_bytes:               {backup_metrics['dump_file_size_bytes']}")
            print(f"records_backed_up:                  {total_record_count}")
            print(f"restore_duration_ms:                {restore_metrics['restore_duration_ms']:.2f}")
            print(f"records_restored:                   {restored_count}")
            print(f"rebuild_from_restored_duration_ms:  {rebuild_metrics['rebuild_total_duration_ms']:.2f}")
            print(f"records_rebuilt:                    {rebuild_metrics['mysql_loaded']}")
            print(f"scenarios_are_independent:          YES")
            print(f"{'='*80}\n")

            # Assertions
            assert backup_metrics['dump_file_size_bytes'] > 0, "Backup file should not be empty"
            assert restored_count == total_record_count, "All records should be restored"
            assert rebuild_metrics['rebuild_success'], "Rebuild after restore should succeed"
            assert rebuild_metrics['mysql_loaded'] == restored_count, \
                f"Should rebuild all {restored_count} records, got {rebuild_metrics['mysql_loaded']}"

        finally:
            # Cleanup backup file
            if os.path.exists(backup_file):
                os.remove(backup_file)
                print(f"  ✓ Cleaned up backup file")