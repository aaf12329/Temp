/** 状态合同回归：Node test → 流程reducer → 分支/确认隔离；纯合成数据，无网络。 */
import test from "node:test";
import assert from "node:assert/strict";
import { initialState, reduceFlow } from "../src/lib/research-flow.ts";
import { DEMO_OBSERVATIONS, demoMetrics, demoPredictionsCsv, generatedCount } from "../src/lib/demo-analysis.ts";

/** 完整推进四个手动训练阶段。 */
function finish(state, type) { for (let index = 0; index < 4; index++) state = reduceFlow(state, { type }); return state; }

test("数据不足只进入整理结果，不能启动建模", () => {
  let state = reduceFlow(initialState(), { type: "submit" });
  for (let index = 0; index < 5; index++) state = reduceFlow(state, { type: "next-data" });
  assert.equal(state.view, "result");
  assert.equal(reduceFlow(state, { type: "navigate", view: "model" }).view, "result");
  assert.equal(reduceFlow(state, { type: "start-model" }).view, "result");
});

test("GAN生成后必须确认；失败批次不可采用，可以改用真实训练数据", () => {
  let state = reduceFlow(initialState(), { type: "preview", view: "model" });
  state = reduceFlow(state, { type: "start-model" });
  assert.equal(state.view, "gan");
  assert.equal(state.ganAccepted, null);
  state = finish(state, "next-gan");
  assert.equal(state.view, "gan-review");
  state = reduceFlow(state, { type: "quality", quality: "fail" });
  assert.equal(reduceFlow(state, { type: "accept-gan", accept: true }).view, "gan-review");
  state = reduceFlow(state, { type: "accept-gan", accept: false });
  assert.equal(state.view, "training");
  assert.equal(state.ganAccepted, false);
  assert.equal(finish(state, "next-training").view, "evaluation");
});

test("采用GAN后返回选择或切换数据分支会清除旧确认与结果", () => {
  let state = reduceFlow(initialState(), { type: "preview", view: "gan-review" });
  state = reduceFlow(state, { type: "accept-gan", accept: true });
  assert.equal(state.ganAccepted, true);
  state = finish(state, "next-training");
  const old = state;
  state = reduceFlow(state, { type: "navigate", view: "model" });
  assert.equal(state.ganAccepted, null);
  assert.equal(state.trainingPhase, 0);
  assert.equal(old.ganAccepted, true);
  state = reduceFlow(old, { type: "scenario", scenario: "data-only" });
  assert.equal(state.view, "input");
  assert.equal(state.ganAccepted, null);
});

test("不使用GAN直接训练；训练失败禁止前进，重试恢复", () => {
  let state = reduceFlow(initialState(), { type: "preview", view: "model" });
  state = reduceFlow(state, { type: "choice", choice: { ...state.choice, useGan: false } });
  state = reduceFlow(state, { type: "start-model" });
  assert.equal(state.view, "training");
  assert.equal(state.ganAccepted, false);
  state = reduceFlow(state, { type: "fail-training" });
  assert.equal(reduceFlow(state, { type: "next-training" }).trainingPhase, 0);
  state = reduceFlow(state, { type: "retry-training" });
  assert.equal(state.trainingError, false);
  assert.equal(finish(state, "next-training").view, "evaluation");
});

test("按真实训练数量计算生成量，CSV与指标使用同一组合成观测", () => {
  assert.equal(generatedCount(24, 5), 120);
  assert.equal(generatedCount(24, .5), 12);
  assert.equal(generatedCount(24, -1), 0);
  assert.equal(generatedCount(24, Infinity), 0);
  const metrics = demoMetrics(DEMO_OBSERVATIONS, true);
  const csv = demoPredictionsCsv(true).trim().split(/\r?\n/);
  assert.equal(csv.length, 9);
  const errors = csv.slice(1).map((line) => Number(line.split(",")[3]));
  assert.equal(metrics.mae, errors.reduce((sum, value) => sum + Math.abs(value), 0) / 8);
  assert.ok(Math.abs(metrics.rmse - Math.sqrt(errors.reduce((sum, value) => sum + value ** 2, 0) / 8)) < 1e-12);
});
