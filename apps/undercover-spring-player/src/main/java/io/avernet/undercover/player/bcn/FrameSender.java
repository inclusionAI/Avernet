package io.avernet.undercover.player.bcn;

import com.fasterxml.jackson.databind.JsonNode;

@FunctionalInterface
public interface FrameSender {

    void send(JsonNode frame);
}
