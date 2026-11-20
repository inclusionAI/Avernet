"""Real OAuth/HTTP journey for Human friend admission in ordinary Groups.

Run on both companion storage modes. Every Bot, friendship and Group comes
from a public request; no test writes bypass the authorization or store layers.
"""
from urllib.parse import quote, urlencode

PUBLIC = '/openapi/v1/collaboration'


def request(browser, base, method, path, body=None, expected=200):
    result, _, status = browser.request(method, base + path, expected,
        {'Origin': base, 'Content-Type': 'application/json'}, body)
    return result['data'] if status < 400 else result


def register_bot(browser, base, name, visibility):
    token = request(browser, base, 'GET', PUBLIC + '/register/token')['token']
    bot = request(browser, base, 'POST', PUBLIC + '/register?' +
        urlencode({'token': token, 'bot_name': name}), expected=201)
    bot_id = bot['bot_uuid']
    updated = request(browser, base, 'PATCH', PUBLIC + '/bots/' + quote(bot_id, safe=''), {
        'visibility': visibility, 'user_visibility': visibility,
    })
    assert updated['visibility'] == visibility, updated
    return bot_id


def assert_group(group, human_id, driver, bots):
    assert group['kind'] == 'normal' and group['visibility'] == 'private', group
    assert group['originator_actor_id'] == human_id, group
    assert group['driver_bot_uuid'] == driver, group
    roles = {entry['actor_id']: entry['role'] for entry in group['participants']}
    assert set(roles) == {human_id, *bots}, group
    assert roles[driver] == 'driver' and roles[human_id] == 'observer', group


def human_friend_group_creation(env, base):
    alice, bob = env.browser(), env.browser()
    alice.login(base, user='friend-group-alice')
    bob.login(base, user='friend-group-bob')
    alice_id = request(alice, base, 'GET', '/openapi/v1/auth/user')['user_id']
    mine = request(alice, base, 'GET', PUBLIC + '/bots/mine')
    human = next(entry['bot_id'] for entry in mine['items'] if entry['kind'] == 'human')
    lead = register_bot(alice, base, 'Friend Group Lead', 'public')
    friend = register_bot(bob, base, 'Friend Group Participant', 'protected')
    friend_path = PUBLIC + '/bots/' + quote(friend, safe='')
    connections = PUBLIC + '/friend-connections'

    def create_group(driver, other=None, expected=201):
        participants = [{'actor_id': human, 'role': 'observer'}]
        if other:
            participants.append({'actor_id': other, 'role': 'consultant'})
        return request(alice, base, 'POST', PUBLIC + '/groups', {
            'group_kind': 'normal', 'name': 'Human friend collaboration',
            'originator': human, 'driver_bot_uuid': driver, 'participants': participants,
            'collaboration': {'strategy': 'chat',
                              'delivery_policy': {'bot_final_delivery': 'send_to_driver'}},
        }, expected=expected)

    def friend_ids():
        page = request(alice, base, 'GET', connections + '?' + urlencode({
            'actor_type': 'human', 'actor_id': alice_id,
        }))
        return {entry['actor']['id'] for entry in page['items']}

    create_group(lead, friend, expected=403)
    pending = request(alice, base, 'POST', connections + '/requests', {
        'from_actor': {'type': 'human', 'id': alice_id},
        'to_actor': {'type': 'bot', 'id': friend},
    }, 201)
    assert pending['status'] == 'pending', pending
    create_group(lead, friend, expected=403)
    request(bob, base, 'POST', connections + f"/requests/{pending['request_ids'][0]}/accept", {})
    assert friend in friend_ids(), 'accepted Bot must be a friend of the Human'

    group = create_group(lead, friend)
    assert_group(group, human, lead, {lead, friend})
    assert next(entry for entry in group['participants'] if entry['actor_id'] == friend)['role'] == 'consultant'

    # Private visibility must not invalidate an active friend edge. The same
    # group admission rule must also apply when the friend is the lead Bot.
    updated = request(bob, base, 'PATCH', friend_path, {
        'visibility': 'private', 'user_visibility': 'private',
    })
    assert updated['visibility'] == 'private', updated
    assert friend in friend_ids(), 'changing visibility must not remove friendship'
    private_group = create_group(friend)
    group_path = PUBLIC + '/groups/' + quote(private_group['group_id'], safe='')
    assert_group(private_group, human, friend, {friend})
    assert_group(request(alice, base, 'GET', group_path), human, friend, {friend})

    updated = request(bob, base, 'PATCH', friend_path, {'status': 'hidden'})
    assert updated['status'] == 'hidden', updated
    create_group(friend, expected=403)
    request(bob, base, 'PATCH', friend_path, {'status': 'online'})
    assert friend in friend_ids(), 'friendship must still be active after restoring status'

    request(alice, base, 'DELETE', connections + '?' + urlencode({
        'target_actor_type': 'bot', 'target_actor_id': friend,
    }))
    assert friend not in friend_ids(), 'revoked Bot must no longer be a friend'
    create_group(friend, expected=403)
