"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  createProvider,
  deleteProvider,
  getLlmCalls,
  listProviders,
  testProvider,
  updateProvider,
  type LlmCalls,
  type Provider,
} from "@/lib/api";

const LOAD_FAILED = "取 provider 列表时出了意外错误";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

type Draft = {
  name: string;
  baseUrl: string;
  apiKey: string;
  defaultModel: string;
  enabled: boolean;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  baseUrl: "",
  apiKey: "",
  defaultModel: "",
  enabled: true,
};

export default function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [tests, setTests] = useState<Record<number, { ok: boolean; detail: string }>>({});
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);

  const [calls, setCalls] = useState<LlmCalls | null>(null);
  const [callsPending, setCallsPending] = useState(false);
  const [callsError, setCallsError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [setAsDefault, setSetAsDefault] = useState(false);

  useEffect(() => {
    listProviders()
      .then((data) => {
        setProviders(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  async function refresh() {
    try {
      setProviders(await listProviders());
      setLoadError(null);
    } catch (cause) {
      setLoadError(messageOf(cause, LOAD_FAILED));
    }
  }

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      const created = await createProvider({
        name,
        baseUrl: baseUrl === "" ? undefined : baseUrl,
        apiKey: apiKey === "" ? undefined : apiKey,
        defaultModel: defaultModel === "" ? undefined : defaultModel,
        setAsDefault,
      });
      setFeedback({
        ok: true,
        text: `已添加 #${created.id}「${created.name}」` + (created.is_default ? "，并设为默认" : ""),
      });
      setName("");
      setBaseUrl("");
      setApiKey("");
      setDefaultModel("");
      setSetAsDefault(false);
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "新增失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onTest(provider: Provider) {
    setPending(true);
    setFeedback(null);
    try {
      const result = await testProvider(provider.id);
      setTests((previous) => ({ ...previous, [provider.id]: result }));
    } catch (cause) {
      setTests((previous) => ({
        ...previous,
        [provider.id]: { ok: false, detail: messageOf(cause, "体检请求失败") },
      }));
    } finally {
      setPending(false);
    }
  }

  async function onSetDefault(provider: Provider) {
    setPending(true);
    setFeedback(null);
    try {
      await updateProvider(provider.id, { setAsDefault: true });
      setFeedback({ ok: true, text: `已将「${provider.name}」设为默认` });
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "设为默认失败") });
    } finally {
      setPending(false);
    }
  }

  async function onToggleEnabled(provider: Provider) {
    setPending(true);
    setFeedback(null);
    try {
      const willEnable = !provider.enabled;
      await updateProvider(provider.id, { enabled: willEnable });
      setFeedback({
        ok: true,
        text: `已${willEnable ? "启用" : "停用"}「${provider.name}」`,
      });
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "切换状态失败") });
    } finally {
      setPending(false);
    }
  }

  function startEdit(provider: Provider) {
    setEditingId(provider.id);
    setConfirmingId(null);
    setDraft({
      name: provider.name,
      baseUrl: provider.base_url ?? "",
      apiKey: "",
      defaultModel: provider.default_model ?? "",
      enabled: provider.enabled,
    });
  }

  async function onSaveEdit(event: FormEvent<HTMLFormElement>, id: number) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      await updateProvider(id, {
        name: draft.name,
        baseUrl: draft.baseUrl === "" ? undefined : draft.baseUrl,
        apiKey: draft.apiKey === "" ? undefined : draft.apiKey,
        defaultModel: draft.defaultModel === "" ? undefined : draft.defaultModel,
        enabled: draft.enabled,
      });
      setFeedback({ ok: true, text: `已保存对「${draft.name}」的修改` });
      setEditingId(null);
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "修改失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onDelete(id: number) {
    setPending(true);
    setFeedback(null);
    try {
      await deleteProvider(id);
      setFeedback({ ok: true, text: `已删除 provider #${id}` });
      setConfirmingId(null);
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "删除失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onLoadCalls() {
    setCallsPending(true);
    setCallsError(null);
    try {
      setCalls(await getLlmCalls(50));
    } catch (cause) {
      setCallsError(messageOf(cause, "取调用记账失败"));
    } finally {
      setCallsPending(false);
    }
  }

  function providerLabel(id: number | null): string {
    if (id === null) return "（无 / 已删）";
    const found = providers?.find((item) => item.id === id);
    return found ? `#${found.id} ${found.name}` : `#${id}`;
  }

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "16px" }}>
        <div>
          <h1>LLM 模型与服务商管理</h1>
          <p style={{ color: "var(--text-muted)", fontSize: "13px", margin: 0 }}>
            配置 OpenAI 兼容格式的模型接入。密钥只写不读，所有调用明细进本地流水账。
          </p>
        </div>
      </div>

      {loadError !== null && (
        <div className="alert alert-danger" role="alert">
          <strong>加载失败：</strong>
          {loadError}
        </div>
      )}

      {feedback !== null && (
        <div className={`alert ${feedback.ok ? "alert-success" : "alert-danger"}`} role="status">
          {feedback.text}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "20px" }}>
        {/* 左侧：新增配置 */}
        <div className="card" style={{ height: "fit-content" }}>
          <h2>添加模型服务商</h2>
          <form onSubmit={onCreate} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            <div>
              <label htmlFor="new-name">服务商名称（唯一标签）：</label>
              <input
                id="new-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：DeepSeek / Kimi / Ollama"
                required
                style={{ width: "100%" }}
              />
            </div>
            <div>
              <label htmlFor="new-base-url">API Base URL：</label>
              <input
                id="new-base-url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.deepseek.com/v1"
                style={{ width: "100%" }}
              />
            </div>
            <div>
              <label htmlFor="new-key">API 密钥（密码）：</label>
              <input
                id="new-key"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="sk-..."
                style={{ width: "100%" }}
              />
              <small style={{ color: "var(--text-muted)" }}>写入后仅前端掩码显示，安全存储</small>
            </div>
            <div>
              <label htmlFor="new-model">默认模型标识：</label>
              <input
                id="new-model"
                value={defaultModel}
                onChange={(event) => setDefaultModel(event.target.value)}
                placeholder="例如 deepseek-chat"
                style={{ width: "100%" }}
              />
            </div>
            <div className="flex-row gap-sm" style={{ marginTop: "4px" }}>
              <input
                type="checkbox"
                id="set-as-default"
                checked={setAsDefault}
                onChange={(event) => setSetAsDefault(event.target.checked)}
              />
              <label htmlFor="set-as-default" style={{ margin: 0, cursor: "pointer" }}>
                设为决策与对话的全局默认
              </label>
            </div>
            <div className="flex-between" style={{ marginTop: "8px" }}>
              <small>支持任意兼容 API</small>
              <button type="submit" className="primary" disabled={pending || name.trim() === ""}>
                {pending ? "正在保存…" : "添加服务商"}
              </button>
            </div>
          </form>
        </div>

        {/* 右侧：列表与管理 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div className="flex-between">
            <h2>已配置的服务商 ({providers?.length ?? 0} 家)</h2>
          </div>

          {providers !== null && providers.length === 0 && (
            <div className="card" style={{ textAlign: "center", padding: "30px" }}>
              <p style={{ color: "var(--text-muted)", margin: 0 }}>尚未添加任何模型服务商。</p>
            </div>
          )}

          {(providers ?? []).map((provider) => (
            <div key={provider.id} className="card" style={{ margin: 0, padding: "16px" }}>
              <div className="flex-between" style={{ marginBottom: "8px" }}>
                <div className="flex-row gap-sm">
                  <span className="badge badge-in_progress">#{provider.id}</span>
                  <strong style={{ fontSize: "15px" }}>{provider.name}</strong>
                  {provider.is_default && <span className="badge badge-done">默认服务商</span>}
                  {!provider.enabled && <span className="badge badge-stuck">已停用</span>}
                </div>
                <div className="flex-row gap-sm">
                  <button
                    type="button"
                    className="sm primary"
                    onClick={() => onTest(provider)}
                    disabled={pending}
                  >
                    测连通性
                  </button>
                  {!provider.is_default && (
                    <button
                      type="button"
                      className="sm"
                      onClick={() => onSetDefault(provider)}
                      disabled={pending}
                    >
                      设默认
                    </button>
                  )}
                  <button
                    type="button"
                    className="sm"
                    onClick={() => onToggleEnabled(provider)}
                    disabled={pending}
                  >
                    {provider.enabled ? "停用" : "启用"}
                  </button>
                  <button
                    type="button"
                    className="sm"
                    onClick={() => startEdit(provider)}
                    disabled={pending}
                  >
                    编辑
                  </button>
                  {confirmingId === provider.id ? (
                    <div className="flex-row gap-sm">
                      <button
                        type="button"
                        className="danger sm"
                        onClick={() => onDelete(provider.id)}
                        disabled={pending}
                      >
                        确认删
                      </button>
                      <button
                        type="button"
                        className="sm"
                        onClick={() => setConfirmingId(null)}
                        disabled={pending}
                      >
                        取消
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="danger sm"
                      onClick={() => {
                        setConfirmingId(provider.id);
                        setEditingId(null);
                      }}
                      disabled={pending}
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: "10px",
                  fontSize: "12px",
                  color: "var(--text-muted)",
                  background: "var(--bg-subtle)",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                <div>接口：{provider.base_url ?? "（默认）"}</div>
                <div>默认模型：{provider.default_model ?? "（未指定）"}</div>
                <div>密钥：{provider.api_key_masked ?? "（无密钥）"}</div>
              </div>

              {tests[provider.id] !== undefined && (
                <div
                  className={`alert ${tests[provider.id].ok ? "alert-success" : "alert-danger"}`}
                  style={{ marginTop: "10px", fontSize: "12px", padding: "6px 10px" }}
                >
                  <strong>{tests[provider.id].ok ? "连通正常：" : "连通异常："}</strong>
                  {tests[provider.id].detail}
                </div>
              )}

              {editingId === provider.id && (
                <form
                  onSubmit={(event) => onSaveEdit(event, provider.id)}
                  className="inline-edit-box"
                  style={{ marginTop: "10px" }}
                >
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
                    <div>
                      <label style={{ fontSize: "11px" }}>名称：</label>
                      <input
                        style={{ width: "100%" }}
                        value={draft.name}
                        onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                        required
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: "11px" }}>接口地址：</label>
                      <input
                        style={{ width: "100%" }}
                        value={draft.baseUrl}
                        onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })}
                      />
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
                    <div>
                      <label style={{ fontSize: "11px" }}>换新密钥（留空不改）：</label>
                      <input
                        style={{ width: "100%" }}
                        type="password"
                        value={draft.apiKey}
                        onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
                        placeholder="留空保持原密钥不变"
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: "11px" }}>默认模型：</label>
                      <input
                        style={{ width: "100%" }}
                        value={draft.defaultModel}
                        onChange={(event) => setDraft({ ...draft, defaultModel: event.target.value })}
                      />
                    </div>
                  </div>
                  <div className="flex-row gap-sm" style={{ marginTop: "4px" }}>
                    <button type="submit" className="primary sm" disabled={pending}>
                      保存修改
                    </button>
                    <button type="button" className="sm" onClick={() => setEditingId(null)} disabled={pending}>
                      取消
                    </button>
                  </div>
                </form>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* 底部调用记账区 */}
      <div className="card" style={{ marginTop: "24px" }}>
        <div className="flex-between">
          <div>
            <h2 style={{ margin: 0 }}>LLM 实际调用记账</h2>
            <small>纯本地 SQLite 账单审计，统计 Tokens 消耗与请求耗时</small>
          </div>
          <button type="button" className="sm" onClick={onLoadCalls} disabled={callsPending}>
            {callsPending ? "正在拉取账单…" : "刷新记账明细"}
          </button>
        </div>

        {callsError !== null && (
          <div className="alert alert-danger" style={{ marginTop: "12px" }}>
            {callsError}
          </div>
        )}

        {calls !== null && calls.by_week.length > 0 && (
          <div style={{ marginTop: "16px" }}>
            <h3 style={{ marginBottom: "8px" }}>按周消耗汇总</h3>
            <table>
              <thead>
                <tr>
                  <th>周期（周）</th>
                  <th>调用总次数</th>
                  <th>成功 / 失败</th>
                  <th>输入 / 输出 Tokens</th>
                  <th>总耗时</th>
                </tr>
              </thead>
              <tbody>
                {calls.by_week.map((bucket) => (
                  <tr key={bucket.week}>
                    <td>{bucket.week}</td>
                    <td>{bucket.calls} 次</td>
                    <td>
                      {bucket.ok} 成功 / {bucket.failed} 失败
                    </td>
                    <td>
                      {bucket.input_tokens} / {bucket.output_tokens}
                    </td>
                    <td>{(bucket.duration_ms / 1000).toFixed(1)} 秒</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3 style={{ marginTop: "20px", marginBottom: "8px" }}>最近 50 条调用流水</h3>
            <div style={{ maxHeight: "360px", overflowY: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>服务商</th>
                    <th>模型</th>
                    <th>业务任务</th>
                    <th>Tokens</th>
                    <th>耗时</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {calls.calls.map((call) => (
                    <tr key={call.id}>
                      <td style={{ fontSize: "11px" }}>{call.created_at.slice(0, 16).replace("T", " ")}</td>
                      <td>{providerLabel(call.provider_id)}</td>
                      <td>{call.model}</td>
                      <td>
                        <span className="badge badge-not_started">{call.task}</span>
                      </td>
                      <td style={{ fontSize: "11px" }}>
                        {call.input_tokens ?? 0} / {call.output_tokens ?? 0}
                      </td>
                      <td>{call.duration_ms} ms</td>
                      <td>
                        <span className={`badge ${call.ok ? "badge-done" : "badge-stuck"}`}>
                          {call.ok ? "成功" : "失败"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
