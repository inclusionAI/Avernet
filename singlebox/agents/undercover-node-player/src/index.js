import WebSocket from "ws";
import { UndercoverNodeAgent, DEFAULT_PERSONA } from "./agent.js";
import { BcnProtocol } from "./protocol.js";

const bcsUrl = process.env.BCS_URL || "ws://127.0.0.1:21000/ws/bot";
const persona = process.env.BOT_PERSONA || DEFAULT_PERSONA;
const agent = new UndercoverNodeAgent({ persona });
const socket = new WebSocket(bcsUrl);
const protocol = new BcnProtocol({
  environment: process.env,
  onTask: (context) => agent.decide(context),
  onError: (error) => console.error(`[node-player] ${error.message}`),
});

socket.once("open", async () => {
  try {
    const connected = await protocol.connect(socket);
    console.log(`[node-player] connected bot_uuid=${connected.bot_uuid} protocol_version=3`);
    if (connected.is_new) {
      console.warn("[node-player] new identity must be onboarded before joining a table");
    }
  } catch (error) {
    console.error(`[node-player] handshake failed: ${error.message}`);
    socket.close();
  }
});

socket.on("error", (error) => {
  console.error(`[node-player] WebSocket error: ${error.message}`);
});

function shutdown() {
  protocol.stop();
  socket.close();
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
