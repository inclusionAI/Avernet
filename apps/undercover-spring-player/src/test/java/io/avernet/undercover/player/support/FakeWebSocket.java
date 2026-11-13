package io.avernet.undercover.player.support;

import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicBoolean;

public final class FakeWebSocket implements WebSocket {

    private final Listener listener;
    private final List<String> sent = new CopyOnWriteArrayList<>();
    private final AtomicBoolean aborted = new AtomicBoolean();
    private volatile java.util.function.Consumer<String> onSend = ignored -> { };

    public FakeWebSocket(Listener listener) {
        this.listener = listener;
    }

    public void setOnSend(java.util.function.Consumer<String> onSend) {
        this.onSend = onSend;
    }

    public void emit(String text) {
        listener.onText(this, text, true);
    }

    public List<String> sent() {
        return sent;
    }

    public boolean isAborted() {
        return aborted.get();
    }

    @Override
    public void request(long n) {
    }

    @Override
    public CompletableFuture<WebSocket> sendText(CharSequence data, boolean last) {
        String text = data.toString();
        sent.add(text);
        try {
            onSend.accept(text);
        } catch (RuntimeException e) {
            return CompletableFuture.failedFuture(e);
        }
        return CompletableFuture.completedFuture(this);
    }

    @Override
    public CompletableFuture<WebSocket> sendBinary(ByteBuffer data, boolean last) {
        return CompletableFuture.completedFuture(this);
    }

    @Override
    public CompletableFuture<WebSocket> sendPing(ByteBuffer message) {
        return CompletableFuture.completedFuture(this);
    }

    @Override
    public CompletableFuture<WebSocket> sendPong(ByteBuffer message) {
        return CompletableFuture.completedFuture(this);
    }

    @Override
    public CompletableFuture<WebSocket> sendClose(int statusCode, String reason) {
        return CompletableFuture.completedFuture(this);
    }

    @Override
    public boolean isOutputClosed() {
        return false;
    }

    @Override
    public boolean isInputClosed() {
        return false;
    }

    @Override
    public String getSubprotocol() {
        return null;
    }

    @Override
    public void abort() {
        aborted.set(true);
    }
}
