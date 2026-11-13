package io.avernet.undercover.player.config;

import java.net.URI;
import java.nio.file.Path;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "bcn")
public class BcnProperties {

    private URI url = URI.create("ws://127.0.0.1:21000/ws/bot");
    private String clientKind = "custom-engine";
    private String botId = "";
    private String token = "";
    private Path sessionFile = Path.of(".bcs/session.json");
    private Duration heartbeatInterval = Duration.ofSeconds(60);
    private Duration connectTimeout = Duration.ofSeconds(30);
    private Duration reconnectInitial = Duration.ofSeconds(1);
    private Duration reconnectMax = Duration.ofSeconds(30);

    public URI getUrl() {
        return url;
    }

    public void setUrl(URI url) {
        this.url = url;
    }

    public String getClientKind() {
        return clientKind;
    }

    public void setClientKind(String clientKind) {
        this.clientKind = clientKind;
    }

    public String getBotId() {
        return botId;
    }

    public void setBotId(String botId) {
        this.botId = botId;
    }

    public String getToken() {
        return token;
    }

    public void setToken(String token) {
        this.token = token;
    }

    public Path getSessionFile() {
        return sessionFile;
    }

    public void setSessionFile(Path sessionFile) {
        this.sessionFile = sessionFile;
    }

    public Duration getHeartbeatInterval() {
        return heartbeatInterval;
    }

    public void setHeartbeatInterval(Duration heartbeatInterval) {
        this.heartbeatInterval = heartbeatInterval;
    }

    public Duration getConnectTimeout() {
        return connectTimeout;
    }

    public void setConnectTimeout(Duration connectTimeout) {
        this.connectTimeout = connectTimeout;
    }

    public Duration getReconnectInitial() {
        return reconnectInitial;
    }

    public void setReconnectInitial(Duration reconnectInitial) {
        this.reconnectInitial = reconnectInitial;
    }

    public Duration getReconnectMax() {
        return reconnectMax;
    }

    public void setReconnectMax(Duration reconnectMax) {
        this.reconnectMax = reconnectMax;
    }
}
