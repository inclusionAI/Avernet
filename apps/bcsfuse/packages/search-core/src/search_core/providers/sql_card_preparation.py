"""Atomic DB leases and artifact publication; model calls never hold a DB lock."""

from search_core.catalog.documents import retired_documents, source_id
from search_core.providers.sql_index_documents import TABLE


class CardPreparation:
    """Methods mixed into the environment-bound SQL Card repository."""

    document_table = TABLE

    def _retire_documents(self, cursor, agent_id, revision):
        """Publish removal to all generations in the source deletion transaction."""
        self._execute(
            cursor,
            f"UPDATE {self.document_table} SET is_deleted=1, source_revision=?, version=version+1, "
            f"gmt_modify={self._next_modified} WHERE environment=? AND source_id=?",
            (revision, self.environment, agent_id),
        )

    def index_documents(self, index_key):
        from search_core.providers.sql_index_documents import SqlIndexDocuments

        return SqlIndexDocuments(
            self.connect,
            environment=self.environment,
            index_key=index_key,
            placeholder=self.placeholder,
            table=self.document_table,
        )

    @property
    def _now(self):
        return self.index_documents("clock").now_expression

    def claim_preparation(
        self, agent_id, revision, index_key, token, lease_seconds, *, force=False
    ):
        with self._cursor() as (conn, cur):
            self._execute(
                cur,
                f"""UPDATE {self.table}
                SET gmt_modify=gmt_modify, preparation_token=?, preparation_key=?, lease_until={self._now}+?
                WHERE environment=? AND agent_id=? AND revision=?
                AND (preparation_token='' OR lease_until<{self._now})
                AND (?=1 OR NOT EXISTS (SELECT 1 FROM {self.document_table} d WHERE d.environment=?
                    AND d.index_key=? AND d.id=? AND d.source_revision=?))""",
                (
                    token,
                    index_key,
                    lease_seconds,
                    self.environment,
                    agent_id,
                    revision,
                    int(force),
                    self.environment,
                    index_key,
                    agent_id,
                    revision,
                ),
            )
            won = cur.rowcount == 1
            conn.commit()
            return won

    def renew_preparation(self, agent_id, revision, index_key, token, lease_seconds):
        with self._cursor() as (conn, cur):
            self._execute(
                cur,
                f"""UPDATE {self.table} SET gmt_modify=gmt_modify, lease_until={self._now}+?
                WHERE environment=? AND agent_id=? AND revision=?
                AND preparation_key=? AND preparation_token=? AND lease_until>{self._now}""",
                (lease_seconds, self.environment, agent_id, revision, index_key, token),
            )
            renewed = cur.rowcount == 1
            conn.commit()
            return renewed

    def release_preparation(self, agent_id, revision, index_key, token, code):
        with self._cursor() as (conn, cur):
            self._execute(
                cur,
                f"""UPDATE {self.table}
                SET gmt_modify=gmt_modify, preparation_token='', lease_until=0, error_code=?
                WHERE environment=? AND agent_id=? AND revision=?
                AND preparation_key=? AND preparation_token=?""",
                (code, self.environment, agent_id, revision, index_key, token),
            )
            conn.commit()

    def publish_document(self, document, index_key, token):
        return self.publish_documents([document], index_key, token)

    def publish_documents(self, documents, index_key, token):
        document = documents[0]
        source = source_id(document)
        if document.id != source or any(
            source_id(d) != source or d.source_revision != document.source_revision
            for d in documents
        ):
            raise ValueError("document group must share one source and revision")
        with self._cursor() as (conn, cur):
            # Pools may enable autocommit; the fence and artifact must still
            # commit together. No model call runs inside this transaction.
            cur.execute(
                "BEGIN IMMEDIATE" if self.placeholder == "?" else "START TRANSACTION"
            )
            # UPDATE acquires the source row lock until artifact commit. A new
            # Card revision or expired lease fences this writer out atomically.
            self._execute(
                cur,
                f"""UPDATE {self.table}
                SET gmt_modify=gmt_modify, preparation_token='', lease_until=0, error_code='',
                    prepared_revision=?
                WHERE environment=? AND agent_id=? AND revision=?
                AND preparation_key=? AND preparation_token=? AND lease_until>{self._now}""",
                (
                    document.source_revision,
                    self.environment,
                    source,
                    document.source_revision,
                    index_key,
                    token,
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return False
            store = self.index_documents(index_key)
            self._execute(
                cur,
                f"SELECT {store.columns} FROM {self.document_table} WHERE environment=? AND index_key=? AND id=?",
                (*store.scope, source),
            )
            previous_main = store.decode(cur.fetchone())
            previous = []
            if previous_main is not None:
                ids = previous_main.metadata.get("document_ids", [previous_main.id])
                marks = ",".join("?" for _ in ids)
                self._execute(
                    cur,
                    f"SELECT {store.columns} FROM {self.document_table} WHERE environment=? AND index_key=? AND id IN ({marks})",
                    (*store.scope, *ids),
                )
                previous = [store.decode(row) for row in cur.fetchall()]
            for item in [*documents, *retired_documents(previous, documents)]:
                self._execute(
                    cur,
                    f"SELECT {store.columns} FROM {self.document_table} WHERE environment=? AND index_key=? AND id=?",
                    (*store.scope, item.id),
                )
                existing = store.decode(cur.fetchone())
                if existing is not None and source_id(existing) != source:
                    raise ValueError("document identity belongs to another source")
                if not store.save_in_transaction(cur, item):
                    conn.rollback()
                    return False
            conn.commit()
            return True
