package io.avernet.undercover.player.bcn;

import java.net.URI;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;

@FunctionalInterface
public interface WebSocketConnector {

    CompletableFuture<WebSocket> connect(URI uri, WebSocket.Listener listener, Duration timeout);
}
