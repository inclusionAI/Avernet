use super::*;
use bcs_service_api::types::bot_profile::{AvatarChange, ProfileWrite};
use serde_json::{Value, json};

fn mine_command() -> ListMyBots {
    ListMyBots {
        caller: human_caller("staff-1"),
        kind: None,
        name: None,
        status: None,
        reachability: None,
        offset: 0,
        limit: 20,
    }
}

#[tokio::test]
async fn mine_returns_current_avatar_and_explicit_null_for_missing_and_human() {
    let fixture = Fixture::new();
    for id in ["with-avatar", "without-avatar"] {
        fixture
            .add_controlled_bot(id, "staff-1", "public", ActorStatus::Online)
            .await;
    }
    for (change, expected) in [
        (AvatarChange::Set("https://example.com/first.png".into()), json!("https://example.com/first.png")),
        (AvatarChange::Set("https://example.com/updated.png".into()), json!("https://example.com/updated.png")),
        (AvatarChange::Clear, Value::Null),
    ] {
        assert_eq!(
            fixture.repo.update_avatar_existing("with-avatar", change).await.expect("write avatar"),
            ProfileWrite::Updated,
        );
        let page = fixture.service.list_mine(mine_command()).await.expect("mine");
        assert_eq!(page.total, 3);
        let items = serde_json::to_value(&page.items).expect("serialize bots");
        for (id, avatar) in [
            ("with-avatar", expected),
            ("without-avatar", Value::Null),
            ("human_staff-1", Value::Null),
        ] {
            let item = items.as_array().expect("items").iter()
                .find(|item| item["bot_id"] == id).expect("listed bot");
            assert_eq!(item.get("avatar_url"), Some(&avatar), "avatar for {id}");
        }
    }
}

#[tokio::test]
async fn mine_reads_only_the_returned_physical_page_and_propagates_avatar_failures() {
    let fixture = Fixture::new();
    for id in ["selected", "outside-page"] {
        fixture.add_controlled_bot(id, "staff-1", "public", ActorStatus::Online).await;
        fixture.repo.update_avatar_existing(id, AvatarChange::Set(format!("https://example.com/{id}.png")))
            .await.expect("write avatar");
    }
    let corrupt_path = fixture._temp.path().join("outside-page/bot.json");
    std::fs::write(&corrupt_path, "invalid JSON").expect("corrupt outside-page profile");
    let mut command = mine_command();
    command.name = Some("selected".into());
    let page = fixture.service.list_mine(command).await.expect("unreturned avatar is not read");
    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].bot_id(), "selected");
    let mut command = mine_command();
    command.kind = Some(BotKind::Human);
    assert_eq!(fixture.service.list_mine(command).await.expect("human-only page").total, 1);
    let mut command = mine_command();
    command.offset = 100;
    assert!(fixture.service.list_mine(command).await.expect("empty page").items.is_empty());
    let error = fixture.service.list_mine(mine_command()).await
        .expect_err("a returned Bot's unreadable avatar must fail the response");
    assert_eq!(error.code(), "internal_error");
}

#[tokio::test]
async fn shared_bot_get_and_query_projection_preserves_avatar() {
    let fixture = Fixture::new();
    fixture.add_controlled_bot("bot", "staff-1", "public", ActorStatus::Online).await;
    fixture.repo.update_avatar_existing("bot", AvatarChange::Set("https://example.com/bot.png".into()))
        .await.expect("write avatar");
    let bot = fixture.service.get(GetBot {
        caller: human_caller("staff-1"),
        bot_id: "bot".into(),
    }).await.expect("get");
    let queried = fixture.service.query(QueryBots {
        caller: human_caller("staff-1"),
        bot_ids: vec!["bot".into()],
    }).await.expect("query");
    for bot in [bot, queried.into_iter().next().expect("query bot")] {
        assert_eq!(serde_json::to_value(bot).expect("serialize").get("avatar_url"),
            Some(&json!("https://example.com/bot.png")));
    }
}

#[tokio::test]
async fn mine_maximum_page_does_not_read_avatars_beyond_pagination() {
    let fixture = Fixture::new();
    let ids: Vec<_> = (0..101).map(|i| format!("bot-{i:03}")).collect();
    for id in &ids {
        fixture.add_controlled_bot(id, "staff-1", "public", ActorStatus::Online).await;
        fixture.repo.update_avatar_existing(id, AvatarChange::Set(format!("https://example.com/{id}.png")))
            .await.expect("write avatar");
    }
    let command = || ListMyBots {
        kind: Some(BotKind::Bot),
        limit: 100,
        ..mine_command()
    };
    let first = fixture.service.list_mine(command()).await.expect("maximum page");
    assert_eq!((first.total, first.items.len()), (101, 100));
    let outside = ids.iter().find(|id| !first.items.iter().any(|bot| bot.bot_id() == *id))
        .expect("one Bot beyond the page");
    std::fs::write(fixture._temp.path().join(outside).join("bot.json"), "invalid JSON")
        .expect("corrupt unreturned profile");
    let page = fixture.service.list_mine(command()).await.expect("page-scoped read");
    assert_eq!(page.items, first.items);
    for bot in page.items {
        let expected = json!(format!("https://example.com/{}.png", bot.bot_id()));
        assert_eq!(serde_json::to_value(bot).expect("serialize").get("avatar_url"), Some(&expected));
    }
    let mut next = command();
    next.offset = 100;
    assert!(fixture.service.list_mine(next).await.is_err(), "next page reads the corrupt profile");
}
