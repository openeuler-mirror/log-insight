#!/usr/bin/env node
/**
 * LogInsight ACP 入口: 与 dsh-acp 相同的 standalone harness,
 * 额外支持用 DSH_PROVIDER 选择 pi-ai 提供方路由 (openai / anthropic / openrouter / custom).
 *
 * 依赖 deepseek-harness-acp@0.1.31 的内部结构 (bin.js / bridge.js),
 * 升级该包时要一起复核; 文件由 Dockerfile 复制到包内 lib/ 以解析依赖.
 */
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { boot, installFailLoud, loadEnv, loadOverlayPatches } from '@deepseek-ai/dsh-app-boot';
import * as Acp from '@deepseek-ai/dsh-acp';
import { createBridge } from './bridge.js';
import { DEFAULT_MODEL } from './models.js';

const name = 'deepseek-harness-acp';
const provider = (process.env.DSH_PROVIDER || '').trim() || 'deepseek-official';
const model = (process.env.DSH_MODEL || '').trim() || DEFAULT_MODEL;

installFailLoud(name);
const env = loadEnv(name);
const custom = existsSync(resolve('cordis.yml')) ? resolve('cordis.yml') : undefined;
const config = custom || fileURLToPath(new URL('../assets/cordis.default.yml', import.meta.url));
const patches = custom ? [] : [
  ...loadOverlayPatches(name, fileURLToPath(import.meta.resolve('@deepseek-ai/dsh-base/cordis.patch.yml'))),
  ...loadOverlayPatches(name, fileURLToPath(new URL('../assets/standalone.patch.yml', import.meta.url))),
];

let bridge;
const ctx = await boot(name, config, patches, (root) => {
  root.provide('launchEnvironment', env);
  root.plugin({
    name: 'standalone-acp',
    inject: [...Acp.inject, 'sessionQuery', 'attachments'],
    apply(acpCtx) {
      bridge = createBridge(acpCtx, 'loginsight');
      Acp.apply(acpCtx, { provider, model, stream: bridge.stream });
    },
  });
}, import.meta.url);

let stopping;
const shutdown = () => stopping ??= ctx.fiber.dispose().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
process.once('SIGINT', () => { void shutdown(); });
process.once('SIGTERM', () => { void shutdown(); });
bridge?.closed.then(shutdown).catch((error) => { console.error(error); void shutdown(); });
