from unittest.mock import Mock

from agentclaw.community.adapters.http.desktop.router import (
    CreateDesktopBotRequest,
    auth_status_desktop_bot,
    create_desktop_bot,
)
from agentclaw.community.core.bot_management.avatar import AvatarTooLargeError


def test_desktop_avatar_errors_return_http_400():
    user = Mock(staffId='u', nickName='n')
    service = Mock()
    service.apply_passport_before_create.side_effect = AvatarTooLargeError('Avatar too large')
    response = create_desktop_bot(
        body=CreateDesktopBotRequest(bot_name='b', machine_id='m', avatar_url='oversized'),
        user=user, service=service,
    )
    assert response.status_code == 400
    passport = Mock()
    passport.query_auth_status.return_value = {'status': 'ISSUED'}
    service.create_after_authorization.side_effect = AvatarTooLargeError('Avatar too large')
    response = auth_status_desktop_bot(
        request={'bot_id': 'b', 'machine_id': 'm', 'avatar_url': 'oversized'},
        user=user, service=service, passport_plugin=passport, auth_rel_plugin=Mock(),
    )
    assert response.status_code == 400
