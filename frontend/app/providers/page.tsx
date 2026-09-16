"use client";

import Link from "next/link";
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

/**
 * T11：provider 管理面板。
 *
 * 一页装下 T10 那几条接口的全部操作：列、增、改、删、测连通、设为默认。
 * 故意不拆组件、不做样式——先把"配一家能用的 provider"这条路走通，
 * 与 P2 那三个页面同一个态度。
 *
 * 两条铁律在这页的体现：
 * - **密钥只写不读**：列表里永远是掩码，编辑框的密钥栏永远是空的（空 = 不改密钥）。
 * - **前端不算业务规则**：谁能当默认、停用后还能不能用，都归后端判，
 *   这一页只负责把参数送过去、把后端那句中文 `detail` 显示出来。
 */

const LOAD_FAILED = "取 provider 列表时出了意外错误";

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}

/** 编辑表单的内容。密钥栏恒为空串——界面拿不到明文，想换只能整把换。 */
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
  /** 体检结果按 provider id 存，测完就地显示在那一行下面，不动列表本身。 */
  const [tests, setTests] = useState<Record<number, { ok: boolean; detail: string }>>({});
  /** 正在编辑哪一行；null = 没人被编辑。 */
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  /** 已经点过「删除」、等他再确认一次的那一行——两步删除，防手滑，也不弹系统对话框。 */
  const [confirmingId, setConfirmingId] = useState<number | null>(null);

  /** 调用记账：**点按钮才拉**，不自动请求（免得每次进这一页都白拉一次）。 */
  const [calls, setCalls] = useState<LlmCalls | null>(null);
  const [callsPending, setCallsPending] = useState(false);
  const [callsError, setCallsError] = useState<string | null>(null);

  // 新增表单
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [setAsDefault, setSetAsDefault] = useState(false);

  // setState 放在 .then 回调里而不是 effect 体内同步调用，
  // 否则会被 eslint 的 react-hooks/set-state-in-effect 拦下。
  useEffect(() => {
    listProviders()
      .then((data) => {
        setProviders(data);
        setLoadError(null);
      })
      .catch((cause: unknown) => setLoadError(messageOf(cause, LOAD_FAILED)));
  }, []);

  /** 每次写完都重新取一遍列表：页面上的"默认是谁""谁被停用"就跟着后端走了。 */
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
      const created = await createProvider({ name, baseUrl, apiKey, defaultModel, setAsDefault });
      setFeedback({
        ok: true,
        text: `已新增「${created.name}」#${created.id}，密钥存成 ${created.api_key_masked ?? "（没配）"}`,
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

  function startEdit(provider: Provider) {
    setEditingId(provider.id);
    setConfirmingId(null);
    setDraft({
      name: provider.name,
      // 地址与模型回填现值（好让你在原值上改），密钥栏留空 = 不改
      baseUrl: provider.base_url ?? "",
      apiKey: "",
      defaultModel: provider.default_model ?? "",
      enabled: provider.enabled,
    });
  }

  async function onSaveEdit(event: FormEvent<HTMLFormElement>, providerId: number) {
    event.preventDefault();
    setPending(true);
    setFeedback(null);
    try {
      // draft 里空着的栏位不会进请求体（见 api.ts 的 updateProvider），
      // 所以"没填密钥"就等于"密钥不动"
      const updated = await updateProvider(providerId, draft);
      setFeedback({ ok: true, text: `已保存「${updated.name}」#${updated.id}` });
      setEditingId(null);
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "保存失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onToggleEnabled(provider: Provider) {
    setPending(true);
    setFeedback(null);
    try {
      await updateProvider(provider.id, { enabled: !provider.enabled });
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "启用 / 停用失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onSetDefault(provider: Provider) {
    setPending(true);
    setFeedback(null);
    try {
      await updateProvider(provider.id, { setAsDefault: true });
      setFeedback({ ok: true, text: `已把「${provider.name}」设为默认` });
      await refresh();
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "设为默认失败，原因不明") });
    } finally {
      setPending(false);
    }
  }

  async function onTest(provider: Provider) {
    setPending(true);
    setFeedback(null);
    try {
      // 不通也是 200，所以这里不进 catch；要判的是 result.ok
      const result = await testProvider(provider.id);
      setTests((previous) => ({ ...previous, [provider.id]: result }));
    } catch (cause) {
      setFeedback({ ok: false, text: messageOf(cause, "体检请求本身失败了") });
    } finally {
      setPending(false);
    }
  }

  async function onDelete(providerId: number) {
    setPending(true);
    setFeedback(null);
    try {
      await deleteProvider(providerId);
      setConfirmingId(null);
      setFeedback({ ok: true, text: `已删除 provider #${providerId}（它的调用记账保留）` });
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
      setCalls(await getLlmCalls());
    } catch (cause) {
      setCallsError(messageOf(cause, "取调用记账失败了"));
    } finally {
      setCallsPending(false);
    }
  }

  /**
   * 流水里只有 provider_id，而这一页本来就加载了 provider 列表，顺手换成名字更好认。
   *
   * 查不到名字是**正常情况**：删掉 provider 时账是故意留着的（账是历史），
   * 这时回退成光秃秃的 `#id`，不假装它是别的什么。
   */
  function providerLabel(providerId: number): string {
    const found = (providers ?? []).find((item) => item.id === providerId);
    return found === undefined ? `#${providerId}` : `#${providerId}「${found.name}」`;
  }

  return (
    <main>
      <h1>LLM 提供商</h1>
      <p>
        <Link href="/">← 回计划表</Link>
      </p>

      {loadError !== null && (
        <p role="alert">
          <strong>取列表失败：</strong>
          {loadError}
        </p>
      )}

      {loadError === null && providers === null && <p role="status">正在取 provider 列表…</p>}

      {feedback !== null && (
        <p role={feedback.ok ? "status" : "alert"}>
          <strong>{feedback.ok ? "成功：" : "失败："}</strong>
          {feedback.text}
        </p>
      )}

      <section>
        <h2>新增一家</h2>
        <form onSubmit={onCreate}>
          <p>
            <label htmlFor="new-name">名称（必填，全库唯一）：</label>
            <input
              id="new-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </p>
          <p>
            <label htmlFor="new-base-url">接口地址（可选）：</label>
            <input
              id="new-base-url"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.example.com/v1"
            />
          </p>
          <p>
            <label htmlFor="new-key">密钥（可选）：</label>
            <input
              id="new-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
            <br />
            <small>只写不读：存进去之后，这一页再也显示不出明文，只会给你末 4 位。</small>
          </p>
          <p>
            <label htmlFor="new-model">默认模型（可选）：</label>
            <input
              id="new-model"
              value={defaultModel}
              onChange={(event) => setDefaultModel(event.target.value)}
              placeholder="gpt-4o-mini"
            />
            <br />
            <small>没配默认模型的话，测连通性会直接告诉你「测不了」。</small>
          </p>
          <p>
            <label>
              <input
                type="checkbox"
                checked={setAsDefault}
                onChange={(event) => setSetAsDefault(event.target.checked)}
              />
              顺手设为默认
            </label>
          </p>
          <button type="submit" disabled={pending}>
            {pending ? "提交中…" : "新增"}
          </button>
        </form>
      </section>

      <section>
        <h2>已配置（{providers?.length ?? 0} 家）</h2>

        {providers !== null && providers.length === 0 && (
          <p role="status">还没有配过任何 provider。上面新增一家试试。</p>
        )}

        <ul>
          {(providers ?? []).map((provider) => (
            <li key={provider.id}>
              <p>
                <strong>#{provider.id}「{provider.name}」</strong>
                {provider.is_default && <strong>（默认）</strong>}
                {!provider.enabled && <strong>（已停用）</strong>}
                <br />
                地址：{provider.base_url ?? "（没配）"}
                <br />
                模型：{provider.default_model ?? "（没配）"}
                <br />
                密钥：{provider.api_key_masked ?? "（没配）"}
              </p>

              <p>
                <button type="button" onClick={() => onTest(provider)} disabled={pending}>
                  测连通性
                </button>{" "}
                {!provider.is_default && (
                  <>
                    <button
                      type="button"
                      onClick={() => onSetDefault(provider)}
                      disabled={pending}
                    >
                      设为默认
                    </button>{" "}
                  </>
                )}
                <button type="button" onClick={() => onToggleEnabled(provider)} disabled={pending}>
                  {provider.enabled ? "停用" : "启用"}
                </button>{" "}
                <button
                  type="button"
                  onClick={() => startEdit(provider)}
                  disabled={pending}
                >
                  编辑
                </button>{" "}
                {confirmingId === provider.id ? (
                  <>
                    <button type="button" onClick={() => onDelete(provider.id)} disabled={pending}>
                      确认删除
                    </button>{" "}
                    <button type="button" onClick={() => setConfirmingId(null)} disabled={pending}>
                      取消
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setConfirmingId(provider.id);
                      setEditingId(null);
                    }}
                    disabled={pending}
                  >
                    删除
                  </button>
                )}
              </p>

              {tests[provider.id] !== undefined && (
                <p role={tests[provider.id].ok ? "status" : "alert"}>
                  <strong>{tests[provider.id].ok ? "通了：" : "没通："}</strong>
                  {tests[provider.id].detail}
                </p>
              )}

              {editingId === provider.id && (
                <form onSubmit={(event) => onSaveEdit(event, provider.id)}>
                  <fieldset>
                    <legend>改「{provider.name}」</legend>
                    <p>
                      <label htmlFor={`edit-name-${provider.id}`}>名称：</label>
                      <input
                        id={`edit-name-${provider.id}`}
                        value={draft.name}
                        onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                        required
                      />
                    </p>
                    <p>
                      <label htmlFor={`edit-url-${provider.id}`}>接口地址：</label>
                      <input
                        id={`edit-url-${provider.id}`}
                        value={draft.baseUrl}
                        onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })}
                      />
                    </p>
                    <p>
                      <label htmlFor={`edit-key-${provider.id}`}>换一把新密钥：</label>
                      <input
                        id={`edit-key-${provider.id}`}
                        type="password"
                        value={draft.apiKey}
                        onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })}
                        placeholder="留空 = 不改密钥"
                      />
                    </p>
                    <p>
                      <label htmlFor={`edit-model-${provider.id}`}>默认模型：</label>
                      <input
                        id={`edit-model-${provider.id}`}
                        value={draft.defaultModel}
                        onChange={(event) =>
                          setDraft({ ...draft, defaultModel: event.target.value })
                        }
                      />
                    </p>
                    <p>
                      <label>
                        <input
                          type="checkbox"
                          checked={draft.enabled}
                          onChange={(event) =>
                            setDraft({ ...draft, enabled: event.target.checked })
                          }
                        />
                        启用
                      </label>
                    </p>
                    <p>
                      <small>
                        留空的栏位 = 不改动。地址与模型没法被清成空——想清空得直接改库。
                      </small>
                    </p>
                    <button type="submit" disabled={pending}>
                      {pending ? "保存中…" : "保存"}
                    </button>{" "}
                    <button type="button" onClick={() => setEditingId(null)} disabled={pending}>
                      取消
                    </button>
                  </fieldset>
                </form>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>调用记账</h2>
        <p>
          <button type="button" onClick={onLoadCalls} disabled={callsPending}>
            {callsPending ? "取账中…" : "查看调用记账"}
          </button>{" "}
          <small>纯读库，看它不会额外花钱。</small>
        </p>

        {callsError !== null && (
          <p role="alert">
            <strong>取账失败：</strong>
            {callsError}
          </p>
        )}

        {calls !== null && calls.by_week.length === 0 && (
          <p role="status">还没有任何调用记录。</p>
        )}

        {calls !== null && calls.by_week.length > 0 && (
          <>
            <h3>按周汇总</h3>
            <table>
              <thead>
                <tr>
                  <th>周</th>
                  <th>次数</th>
                  <th>成功 / 失败</th>
                  <th>tokens 进 / 出</th>
                  <th>总耗时</th>
                </tr>
              </thead>
              <tbody>
                {calls.by_week.map((bucket) => (
                  <tr key={bucket.week}>
                    <td>{bucket.week}</td>
                    <td>{bucket.calls}</td>
                    <td>
                      {bucket.ok} / {bucket.failed}
                    </td>
                    <td>
                      {bucket.input_tokens} / {bucket.output_tokens}
                    </td>
                    <td>{bucket.duration_ms} 毫秒</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>最近流水（{calls.calls.length} 条）</h3>
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>提供商</th>
                  <th>模型</th>
                  <th>任务</th>
                  <th>tokens 进 / 出</th>
                  <th>耗时</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {calls.calls.map((call) => (
                  <tr key={call.id}>
                    <td>{call.created_at}</td>
                    <td>{providerLabel(call.provider_id)}</td>
                    <td>{call.model}</td>
                    <td>{call.task}</td>
                    <td>
                      {call.input_tokens ?? "—"} / {call.output_tokens ?? "—"}
                    </td>
                    <td>{call.duration_ms ?? "—"} 毫秒</td>
                    <td>
                      {call.ok ? "成功" : "失败"}
                      {call.error !== null && (
                        <>
                          <br />
                          <small>{call.error}</small>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
    </main>
  );
}
