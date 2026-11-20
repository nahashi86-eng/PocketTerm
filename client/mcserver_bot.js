"use strict";

// PocketTerm MCServer Bot engine.
// Usage: node mcserver_bot.js host port username version radius

const [host, portText, username, requestedVersion, radiusText] = process.argv.slice(2);
const port = Number(portText || 25565);
const radius = Math.max(5, Math.min(128, Number(radiusText || 24)));

function log(message) {
  console.log(`[${new Date().toLocaleTimeString()}] ${message}`);
}

if (!host || !username || !Number.isInteger(port) || port < 1 || port > 65535) {
  console.error("Invalid connection settings.");
  process.exit(2);
}

let mineflayer;
let pathfinder;
let Movements;
let goals;

try {
  mineflayer = require("mineflayer");
  pathfinder = require("mineflayer-pathfinder");
  Movements = pathfinder.Movements;
  goals = pathfinder.goals;
} catch (error) {
  console.error("Required Node packages are missing.");
  console.error("From this folder, run: npm install mineflayer mineflayer-pathfinder");
  console.error(`Details: ${error.message}`);
  process.exit(3);
}

const options = {
  host,
  port,
  username,
  auth: "offline",
};
if (requestedVersion && requestedVersion !== "Auto") {
  options.version = requestedVersion;
}

log(`Connecting to ${host}:${port} as ${username} (offline authentication)...`);
if (options.version) log(`Requested Minecraft version: ${options.version}`);
else log("Minecraft version: automatic detection");

const bot = mineflayer.createBot(options);
let origin = null;
let wanderTimer = null;
let lookTimer = null;
let shuttingDown = false;

bot.loadPlugin(pathfinder.pathfinder);

bot.once("spawn", () => {
  log(`Spawned in the world. Starting random roaming (radius ${radius}).`);
  const movements = new Movements(bot);
  movements.canDig = false;
  movements.allow1by1towers = false;
  bot.pathfinder.setMovements(movements);

  origin = bot.entity.position.clone();
  chooseDestination();
  wanderTimer = setInterval(chooseDestination, 9000);
  lookTimer = setInterval(randomLook, 3500);
});

function chooseDestination() {
  if (!bot.entity || !origin || shuttingDown) return;
  const angle = Math.random() * Math.PI * 2;
  const distance = 4 + Math.random() * Math.max(4, radius - 4);
  const x = Math.round(origin.x + Math.cos(angle) * distance);
  const z = Math.round(origin.z + Math.sin(angle) * distance);
  const y = Math.round(bot.entity.position.y);
  bot.pathfinder.setGoal(new goals.GoalNear(x, y, z, 2));
  log(`Roaming toward a nearby location (${x}, ${y}, ${z}).`);

  if (Math.random() < 0.22) {
    setTimeout(() => {
      if (!shuttingDown && bot.entity) {
        bot.setControlState("jump", true);
        setTimeout(() => bot.setControlState("jump", false), 180);
      }
    }, 500 + Math.random() * 1200);
  }
}

function randomLook() {
  if (!bot.entity || shuttingDown || Math.random() > 0.72) return;
  const yaw = (Math.random() * Math.PI * 2) - Math.PI;
  const pitch = (Math.random() - 0.5) * 0.5;
  bot.look(yaw, pitch, true).catch(() => {});
}

bot.on("kicked", (reason) => log(`Kicked by server: ${String(reason)}`));
bot.on("error", (error) => log(`Connection error: ${error.message}`));
bot.on("end", (reason) => {
  log(`Disconnected: ${reason || "connection ended"}`);
  cleanup();
  process.exitCode = 0;
});

function cleanup() {
  shuttingDown = true;
  if (wanderTimer) clearInterval(wanderTimer);
  if (lookTimer) clearInterval(lookTimer);
  if (bot.pathfinder) bot.pathfinder.setGoal(null);
  if (bot.clearControlStates) bot.clearControlStates();
}

process.on("SIGTERM", () => {
  log("Disconnecting...");
  cleanup();
  try { bot.quit("PocketTerm bot stopped"); } catch (_) { process.exit(0); }
  setTimeout(() => process.exit(0), 1200).unref();
});
process.on("SIGINT", () => {
  cleanup();
  try { bot.quit("PocketTerm bot stopped"); } catch (_) {}
  process.exit(0);
});
