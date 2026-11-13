package io.avernet.undercover.player;

import io.avernet.undercover.player.bcn.WebSocketConnector;
import java.net.http.HttpClient;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.context.annotation.Bean;

@SpringBootApplication
@ConfigurationPropertiesScan
public class UndercoverPlayerApplication {

    public static void main(String[] args) {
        SpringApplication.run(UndercoverPlayerApplication.class, args);
    }

    @Bean
    WebSocketConnector webSocketConnector() {
        HttpClient client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();
        return (uri, listener, timeout) -> client.newWebSocketBuilder()
                .connectTimeout(timeout)
                .buildAsync(uri, listener);
    }
}
