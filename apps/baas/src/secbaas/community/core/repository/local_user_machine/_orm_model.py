from sqlalchemy import BigInteger, Column, DateTime, String, Text, func

from secbaas.community.spi.database import Base

from ._record import LocalUserMachineRecord


class LocalUserMachineModel(Base):
    __tablename__ = "baas_local_user_machine"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    template_id = Column(BigInteger, nullable=False)
    user_id = Column(String(128), nullable=False)
    machine_id = Column(String(128), nullable=False)
    machine_info = Column(Text, nullable=True)
    last_heartbeat = Column(DateTime, nullable=True)
    connected_server_instance = Column(String(256), nullable=True)
    status = Column(String(32), nullable=False)
    env = Column(String(32), nullable=False)
    connected_route_info = Column(Text, nullable=True)

    def to_record(self) -> LocalUserMachineRecord:
        import json

        try:
            mi = (
                json.loads(self.machine_info)
                if isinstance(self.machine_info, str)
                else self.machine_info
            )
        except (json.JSONDecodeError, TypeError):
            mi = {}
        try:
            cri = (
                json.loads(self.connected_route_info)
                if isinstance(self.connected_route_info, str)
                else self.connected_route_info
            )
        except (json.JSONDecodeError, TypeError):
            cri = None
        return LocalUserMachineRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            template_id=self.template_id,
            user_id=self.user_id,
            machine_id=self.machine_id,
            machine_info=mi or {},
            last_heartbeat=self.last_heartbeat,
            connected_server_instance=self.connected_server_instance or "",
            status=self.status,
            env=self.env,
            connected_route_info=cri,
        )
