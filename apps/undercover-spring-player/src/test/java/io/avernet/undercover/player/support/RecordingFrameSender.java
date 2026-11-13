package io.avernet.undercover.player.support;

import com.fasterxml.jackson.databind.JsonNode;
import io.avernet.undercover.player.bcn.FrameSender;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

public final class RecordingFrameSender implements FrameSender {

    private final List<JsonNode> frames = new CopyOnWriteArrayList<>();

    @Override
    public void send(JsonNode frame) {
        frames.add(frame.deepCopy());
    }

    public List<JsonNode> frames() {
        return frames;
    }

    public JsonNode last() {
        return frames.get(frames.size() - 1);
    }
}
