package a2a.agent.client.transport.http;

import java.net.http.HttpClient;

/** 创建默认配置的共享 HttpClient。一个应用创建一个，交给两种 HTTP 传输和 Card 拉取共用。 */
public final class HttpClients {

    private HttpClients() {
    }

    public static HttpClient newDefault() {
        return HttpSupport.defaultClient();
    }
}
