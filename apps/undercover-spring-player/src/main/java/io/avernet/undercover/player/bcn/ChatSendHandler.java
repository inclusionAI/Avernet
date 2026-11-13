package io.avernet.undercover.player.bcn;

import com.fasterxml.jackson.databind.JsonNode;
import io.avernet.undercover.player.run.PlayerRun;

@FunctionalInterface
public interface ChatSendHandler {

    void handle(ChatSendTask task);

    record ChatSendTask(PlayerRun run, String text, JsonNode params) {
    }
}
