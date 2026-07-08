import json

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    String,
    UniqueConstraint,
    func,
)

from secbaas.spi.database import Base

from ._record import WsRelaySessionRecord


class WsRelaySessionModel(Base):
    __tablename__ = "baas_local_ws_relay_session"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    session_id = Column(String(128), nullable=False)
    machine_id = Column(String(128), nullable=False)
    connected_server_instance = Column(String(512), nullable=False)
    status = Column(String(64), nullable=False)
    env = Column(String(16), nullable=False)
    gmt_close = Column(DateTime, nullable=True)
    connected_route_info = Column(String(1024), nullable=False)
    operator = Column(String(128), nullable=False)

    __table_args__ = (UniqueConstraint("session_id", "env", name="uk_env_session"),)

    def to_record(self) -> WsRelaySessionRecord:
        route_info = None
        if self.connected_route_info and isinstance(self.connected_route_info, str):
            stripped = self.connected_route_info.strip()
            if stripped:
                try:
                    route_info = json.loads(stripped)
                except (json.JSONDecodeError, TypeError):
                    route_info = None
        return WsRelaySessionRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            session_id=self.session_id,
            machine_id=self.machine_id,
            connected_server_instance=self.connected_server_instance,
            status=self.status,
            env=self.env,
            gmt_close=self.gmt_close,
            connected_route_info=route_info,
            operator=self.operator,
        )
