"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Check, ChevronDown, Plus, RefreshCw, X } from "lucide-react";
import { ApiError, createProvider, deleteProvider, fetchProviderModels, getLlmCalls, listProviders, testProvider, updateProvider, type LlmCalls, type Provider } from "@/lib/api";
import { WeeklyReminder } from "@/components/settings/weekly-reminder";

const messageOf = (cause: unknown, fallback: string) => cause instanceof ApiError ? cause.message : fallback;
type Draft = {
  name: string; baseUrl: string; apiKey: string; defaultModel: string; enabled: boolean;
  reasoningEffort: string; temperature: string; maxOutputTokens: string; contextWindow: string;
  supportsWebSearch: boolean; supportsImages: boolean; extraBody: string;
};
const EMPTY_DRAFT: Draft = {
  name: "", baseUrl: "", apiKey: "", defaultModel: "", enabled: true,
  reasoningEffort: "", temperature: "", maxOutputTokens: "", contextWindow: "",
  supportsWebSearch: false, supportsImages: false, extraBody: "",
};
const REASONING_OPTIONS: [string, string][] = [
  ["", "不指定（用模型默认）"], ["off", "关闭"], ["minimal", "最低"], ["low", "低"], ["medium", "中"], ["high", "高"],
];
const REASONING_LABEL: Record<string, string> = { off: "关闭", minimal: "最低", low: "低", medium: "中", high: "高" };
/** 常用接入的预填模板：点一下把地址和模型填上，密钥仍要自己给；窗口数是常见配置，可改。 */
const PRESETS: { label: string; name: string; baseUrl: string; defaultModel: string; contextWindow: string }[] = [
  { label: "DeepSeek", name: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", defaultModel: "deepseek-chat", contextWindow: "131072" },
  { label: "智谱 GLM", name: "智谱", baseUrl: "https://open.bigmodel.cn/api/paas/v4", defaultModel: "glm-4.6", contextWindow: "204800" },
  { label: "Kimi", name: "Moonshot", baseUrl: "https://api.moonshot.cn/v1", defaultModel: "kimi-k2-turbo-preview", contextWindow: "262144" },
  { label: "通义千问", name: "通义千问", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", defaultModel: "qwen-plus", contextWindow: "131072" },
  { label: "硅基流动", name: "硅基流动", baseUrl: "https://api.siliconflow.cn/v1", defaultModel: "deepseek-ai/DeepSeek-V3", contextWindow: "" },
  { label: "OpenRouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", defaultModel: "", contextWindow: "" },
  { label: "Ollama 本地", name: "Ollama", baseUrl: "http://127.0.0.1:11434/v1", defaultModel: "", contextWindow: "" },
];
/** 空串 = 清掉这个设置；填了但不是数字 NaN 传出去，由保存前的校验拦下。 */
const numberOrNull = (value: string) => {
  const text = value.trim();
  if (text === "") return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : NaN;
};
const settingsOf = (draft: Draft) => ({
  reasoningEffort: draft.reasoningEffort || null,
  temperature: numberOrNull(draft.temperature),
  maxOutputTokens: numberOrNull(draft.maxOutputTokens),
  contextWindow: numberOrNull(draft.contextWindow),
  supportsWebSearch: draft.supportsWebSearch,
  supportsImages: draft.supportsImages,
  extraBody: draft.extraBody,
});

export default function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [tests, setTests] = useState<Record<number, { ok: boolean; detail: string }>>({});
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [mode, setMode] = useState<"detail" | "create" | "edit">("detail");
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [setAsDefault, setSetAsDefault] = useState(false);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [calls, setCalls] = useState<LlmCalls | null>(null);
  const [callsPending, setCallsPending] = useState(false);
  const [callsError, setCallsError] = useState<string | null>(null);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [modelOptions, setModelOptions] = useState<string[] | null>(null);
  const [modelsPending, setModelsPending] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  useEffect(() => {
    listProviders().then((data) => { setProviders(data); setLoadError(null); })
      .catch((cause: unknown) => setLoadError(messageOf(cause, "取服务商列表时出了意外错误")));
  }, []);

  async function refresh() {
    try { setProviders(await listProviders()); setLoadError(null); }
    catch (cause) { setLoadError(messageOf(cause, "取服务商列表时出了意外错误")); }
  }

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setFeedback(null);
    if ([numberOrNull(draft.temperature), numberOrNull(draft.maxOutputTokens), numberOrNull(draft.contextWindow)].some((value) => Number.isNaN(value))) {
      setPending(false);
      setFeedback({ ok: false, text: "温度、输出上限、上下文窗口要填数字，或者留空不设置。" });
      return;
    }
    try {
      if (mode === "create") {
        const created = await createProvider({ name: draft.name, baseUrl: draft.baseUrl || undefined, apiKey: draft.apiKey || undefined, defaultModel: draft.defaultModel || undefined, settings: settingsOf(draft), setAsDefault });
        setSelectedId(created.id); setFeedback({ ok: true, text: `已添加「${created.name}」${created.is_default ? "，并设为默认" : ""}` });
      } else if (mode === "edit" && selectedId !== null) {
        await updateProvider(selectedId, { name: draft.name, baseUrl: draft.baseUrl || undefined, apiKey: draft.apiKey || undefined, defaultModel: draft.defaultModel || undefined, enabled: draft.enabled, settings: settingsOf(draft) });
        setFeedback({ ok: true, text: `已保存「${draft.name}」的修改。` });
      }
      setMode("detail"); setDraft(EMPTY_DRAFT); setSetAsDefault(false); await refresh();
    } catch (cause) { setFeedback({ ok: false, text: messageOf(cause, "保存失败，原因不明") }); }
    finally { setPending(false); }
  }

  async function onTest(provider: Provider) {
    setPending(true); setFeedback(null);
    try { const result = await testProvider(provider.id); setTests((previous) => ({ ...previous, [provider.id]: result })); }
    catch (cause) { setTests((previous) => ({ ...previous, [provider.id]: { ok: false, detail: messageOf(cause, "体检请求失败") } })); }
    finally { setPending(false); }
  }
  async function onFetchModels() {
    if (!draft.baseUrl.trim()) { setModelsError("先填接口地址，才能拉模型列表"); return; }
    setModelsPending(true); setModelsError(null);
    try {
      const models = await fetchProviderModels({
        baseUrl: draft.baseUrl,
        apiKey: draft.apiKey || undefined,
        // 编辑已有接入时界面上没有明文密钥，让后端用库里那把
        providerId: mode === "edit" && selectedId !== null ? selectedId : undefined,
      });
      setModelOptions(models);
      if (models.length > 0 && !draft.defaultModel.trim()) setDraft((previous) => ({ ...previous, defaultModel: models[0] }));
      if (models.length === 0) setModelsError("接口通了，但没返回任何模型。");
    }
    catch (cause) { setModelOptions(null); setModelsError(messageOf(cause, "拉模型列表失败")); }
    finally { setModelsPending(false); }
  }
  async function changeProvider(provider: Provider, action: "default" | "toggle" | "delete") {
    setPending(true); setFeedback(null);
    try {
      if (action === "delete") { await deleteProvider(provider.id); setSelectedId(null); setConfirmingId(null); setFeedback({ ok: true, text: `已删除「${provider.name}」。` }); }
      else if (action === "default") { await updateProvider(provider.id, { setAsDefault: true }); setFeedback({ ok: true, text: `已将「${provider.name}」设为默认。` }); }
      else { await updateProvider(provider.id, { enabled: !provider.enabled }); setFeedback({ ok: true, text: `已${provider.enabled ? "停用" : "启用"}「${provider.name}」。` }); }
      await refresh();
    } catch (cause) { setFeedback({ ok: false, text: messageOf(cause, "操作失败，原因不明") }); }
    finally { setPending(false); }
  }
  async function onLoadCalls() {
    setCallsPending(true); setCallsError(null);
    try { setCalls(await getLlmCalls(50)); }
    catch (cause) { setCallsError(messageOf(cause, "取调用记账失败")); }
    finally { setCallsPending(false); }
  }
  function providerLabel(id: number | null) {
    if (id === null) return "（无 / 已删）";
    const found = providers?.find((item) => item.id === id);
    return found ? `#${found.id} ${found.name}` : `#${id}`;
  }
  const selected = providers?.find((item) => item.id === selectedId) ?? providers?.find((item) => item.is_default) ?? providers?.[0];
  const activeId = selected?.id ?? null;
  function startEdit(provider: Provider) {
    setSelectedId(provider.id); setMode("edit"); setConfirmingId(null); setModelOptions(null); setModelsError(null);
    setDraft({
      name: provider.name, baseUrl: provider.base_url ?? "", apiKey: "", defaultModel: provider.default_model ?? "", enabled: provider.enabled,
      reasoningEffort: provider.reasoning_effort ?? "", temperature: provider.temperature?.toString() ?? "",
      maxOutputTokens: provider.max_output_tokens?.toString() ?? "", contextWindow: provider.context_window?.toString() ?? "",
      supportsWebSearch: provider.supports_web_search, supportsImages: provider.supports_images,
      extraBody: provider.extra_body ?? "",
    });
  }

  return (
    <div className="mx-auto w-full max-w-[1350px] px-5 pb-24 pt-20 text-white md:px-10 md:pt-24">
      <header className="mb-10 flex flex-wrap items-end justify-between gap-6 border-b border-white/[0.12] pb-8"><div><h1 className="text-[32px] font-semibold tracking-[-0.03em] md:text-[42px]">模型接入</h1><p className="mt-3 max-w-[64ch] text-[14px] leading-7 text-white/50">管理兼容接口、检测连接，并查看实际调用。密钥只写入，不会在界面回显明文。</p></div><span className="font-mono text-[12px] text-white/40">{providers ? `${providers.length} 个接入` : "读取中"}</span></header>
      {loadError && <p role="alert" className="mb-6 border-t border-red-400/30 pt-3 text-[13px] text-red-300">加载失败：{loadError}</p>}
      {feedback && <p role={feedback.ok ? "status" : "alert"} className={`mb-6 border-t pt-3 text-[13px] ${feedback.ok ? "border-emerald-400/30 text-emerald-300" : "border-red-400/30 text-red-300"}`}>{feedback.text}</p>}

      <div className="grid gap-9 lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-12">
        <nav aria-label="服务商列表" className="border-b border-white/[0.1] pb-5 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-7"><div className="mb-4 flex items-center justify-between"><h2 className="text-[13px] font-medium text-white/60">已接入</h2><button type="button" onClick={() => { setMode("create"); setDraft(EMPTY_DRAFT); setConfirmingId(null); setModelOptions(null); setModelsError(null); }} aria-label="添加服务商" className="text-white/45 hover:text-white"><Plus className="size-5" /></button></div>{providers === null && !loadError && <p className="text-[12px] text-white/35">正在读取配置…</p>}{providers?.length === 0 && <p className="py-6 text-[12px] leading-6 text-white/40">尚无服务商。点击上方加号建立第一个连接。</p>}<div className="flex gap-2 overflow-x-auto lg:block lg:overflow-visible">{providers?.map((provider) => <button key={provider.id} type="button" onClick={() => { setSelectedId(provider.id); setMode("detail"); setConfirmingId(null); }} aria-current={mode !== "create" && activeId === provider.id ? "page" : undefined} className={`flex min-w-36 shrink-0 items-center justify-between gap-3 border-b px-1 py-4 text-left lg:w-full ${mode !== "create" && activeId === provider.id ? "border-white text-white" : "border-white/[0.08] text-white/45 hover:text-white/80"}`}><span className="min-w-0"><span className="block truncate text-[13px]">{provider.name}</span><span className="mt-1 block text-[11px] opacity-55">{provider.is_default ? "默认" : provider.enabled ? "可用" : "已停用"}</span></span><span className="font-mono text-[11px] opacity-50">#{provider.id}</span></button>)}</div><button type="button" onClick={() => { setMode("create"); setDraft(EMPTY_DRAFT); setModelOptions(null); setModelsError(null); }} className="mt-6 hidden items-center gap-2 text-[12px] text-white/50 hover:text-white lg:inline-flex"><Plus className="size-4" />接入新模型</button></nav>

        <main className="min-w-0">
          {mode === "create" || mode === "edit" ? <section className="max-w-[690px]"><div className="mb-8 flex items-start justify-between border-b border-white/[0.1] pb-5"><div><h2 className="text-[24px] font-medium">{mode === "create" ? "建立一个连接" : `调整「${selected?.name ?? "服务商"}」`}</h2><p className="mt-2 text-[12px] leading-6 text-white/45">{mode === "create" ? "配置写入本地；接口和模型可在之后修改。" : "密钥留空表示保持原值，接口地址和模型留空也不会清除旧值。"}</p></div><button type="button" onClick={() => setMode("detail")} aria-label="关闭配置表单" className="p-1 text-white/45 hover:text-white"><X className="size-5" /></button></div><form onSubmit={onSave} className="space-y-6">{mode === "create" && <div><p className="mb-2 text-[12px] text-white/55">常用接入 · 点一下自动填好地址和模型</p><div className="flex flex-wrap gap-2">{PRESETS.map((preset) => <button key={preset.label} type="button" onClick={() => setDraft({ ...draft, name: preset.name, baseUrl: preset.baseUrl, defaultModel: preset.defaultModel, contextWindow: preset.contextWindow || draft.contextWindow })} className="border border-white/20 px-3 py-1.5 text-[12px] text-white/70 hover:border-white/60 hover:text-white">{preset.label}</button>)}</div></div>}<div className="grid gap-6 sm:grid-cols-2"><Field label="服务商名称" id="provider-name"><input id="provider-name" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如 DeepSeek" required className="provider-input" /></Field><Field label="默认模型标识" id="provider-model">
        <div className="flex gap-3">
          <input id="provider-model" list="model-options" value={draft.defaultModel} onChange={(event) => setDraft({ ...draft, defaultModel: event.target.value })} placeholder="例如 deepseek-chat" className="provider-input" />
          <button type="button" onClick={onFetchModels} disabled={modelsPending} className="inline-flex min-h-10 shrink-0 items-center gap-2 border border-white/25 px-4 text-[12px] text-white/80 hover:border-white/60 disabled:opacity-40"><RefreshCw className={`size-3.5 ${modelsPending ? "animate-spin" : ""}`} />{modelsPending ? "拉取中…" : "拉取模型列表"}</button>
        </div>
        <datalist id="model-options">{modelOptions?.map((id) => <option key={id} value={id} />)}</datalist>
        {modelsError && <p role="alert" className="mt-2 text-[12px] text-red-300">{modelsError}</p>}
        {modelOptions && modelOptions.length > 0 && <p className="mt-2 text-[11px] text-white/40">拿到 {modelOptions.length} 个模型，输入框可以直接下拉选。</p>}
      </Field></div><Field label="接口地址" id="provider-url"><input id="provider-url" value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} placeholder="https://api.example.com/v1" className="provider-input" /></Field><Field label={mode === "edit" ? "换新密钥 · 留空不改" : "API 密钥"} id="provider-key"><input id="provider-key" type="password" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} autoComplete="new-password" placeholder="只写入，不回显" className="provider-input" /></Field>
<div className="border-t border-white/[0.1] pt-6">
  <h3 className="mb-1 text-[14px] font-medium text-white/80">模型设置</h3>
  <p className="mb-5 text-[12px] leading-6 text-white/45">作用于这家接入的默认模型；留空的项不进请求，用模型自己的默认。联网搜索与图片是能力标记，要真正开某家的私有开关就填附加请求体。</p>
  <div className="grid gap-6 sm:grid-cols-2">
    <Field label="思考程度" id="provider-reasoning">
      <select id="provider-reasoning" value={draft.reasoningEffort} onChange={(event) => setDraft({ ...draft, reasoningEffort: event.target.value })} className="provider-input">
        {REASONING_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </Field>
    <Field label="温度 · 留空不指定" id="provider-temperature">
      <input id="provider-temperature" type="number" step="0.1" min="0" max="2" value={draft.temperature} onChange={(event) => setDraft({ ...draft, temperature: event.target.value })} placeholder="0 到 2，例如 0.7" className="provider-input" />
    </Field>
    <Field label="输出上限（tokens）· 留空不设限" id="provider-max-output">
      <input id="provider-max-output" type="number" min="1" value={draft.maxOutputTokens} onChange={(event) => setDraft({ ...draft, maxOutputTokens: event.target.value })} placeholder="例如 4096" className="provider-input" />
    </Field>
    <Field label="上下文窗口（tokens）· 留空不记录" id="provider-context">
      <input id="provider-context" type="number" min="1" value={draft.contextWindow} onChange={(event) => setDraft({ ...draft, contextWindow: event.target.value })} placeholder="例如 128000" className="provider-input" />
    </Field>
  </div>
  <div className="mt-5 flex flex-wrap gap-x-8 gap-y-3 text-[12px] text-white/60">
    <label className="flex items-center gap-3"><input type="checkbox" checked={draft.supportsWebSearch} onChange={(event) => setDraft({ ...draft, supportsWebSearch: event.target.checked })} className="accent-white" />支持原生联网搜索</label>
    <label className="flex items-center gap-3"><input type="checkbox" checked={draft.supportsImages} onChange={(event) => setDraft({ ...draft, supportsImages: event.target.checked })} className="accent-white" />支持图片输入</label>
  </div>
  <div className="mt-5">
    <Field label="附加请求体 · 可选，JSON 对象" id="provider-extra-body">
      <textarea id="provider-extra-body" rows={3} value={draft.extraBody} onChange={(event) => setDraft({ ...draft, extraBody: event.target.value })} placeholder='{"thinking": {"type": "enabled"}}' className="provider-input font-mono text-[12px]" />
    </Field>
    <p className="mt-2 text-[11px] leading-5 text-white/35">逐字并入每次调用的请求体，给各家私有开关用；会和上面的标准设置合并，以后写的为准。</p>
  </div>
</div>
{mode === "create" && <label className="flex items-center gap-3 text-[12px] text-white/60"><input type="checkbox" checked={setAsDefault} onChange={(event) => setSetAsDefault(event.target.checked)} className="accent-white" />设为全局默认服务商</label>}<div className="flex justify-end border-t border-white/[0.1] pt-6"><button type="submit" disabled={pending || !draft.name.trim()} className="inline-flex min-h-10 items-center gap-2 bg-white px-6 text-[13px] font-semibold text-black hover:bg-white/85 disabled:opacity-40">{pending ? "正在保存…" : mode === "create" ? "添加服务商" : "保存修改"}<ArrowRight className="size-4" /></button></div></form></section> : selected ? <section><div className="mb-9 flex flex-wrap items-start justify-between gap-5 border-b border-white/[0.1] pb-6"><div><div className="flex flex-wrap items-center gap-3"><h2 className="text-[27px] font-medium tracking-[-0.02em]">{selected.name}</h2>{selected.is_default && <span className="border border-emerald-300/30 px-2 py-0.5 text-[11px] text-emerald-300">默认连接</span>}{!selected.enabled && <span className="border border-white/20 px-2 py-0.5 text-[11px] text-white/45">已停用</span>}</div><p className="mt-2 font-mono text-[11px] text-white/35">连接 #{selected.id}</p></div><button type="button" onClick={() => onTest(selected)} disabled={pending} className="inline-flex min-h-9 items-center gap-2 border border-white/25 px-4 text-[12px] text-white/80 hover:border-white/60 disabled:opacity-40"><RefreshCw className={`size-3.5 ${pending ? "animate-spin" : ""}`} />测连通性</button></div>
            <dl className="grid gap-x-9 border-b border-white/[0.1] pb-8 sm:grid-cols-2"><Info label="接口地址" value={selected.base_url ?? "默认地址"} /><Info label="默认模型" value={selected.default_model ?? "未指定"} /><Info label="密钥状态" value={selected.api_key_masked ?? "未配置密钥"} /><Info label="使用状态" value={selected.enabled ? "已启用" : "已停用"} /><Info label="思考程度" value={selected.reasoning_effort ? REASONING_LABEL[selected.reasoning_effort] ?? selected.reasoning_effort : "未设置"} /><Info label="温度 / 输出上限" value={`${selected.temperature ?? "—"} / ${selected.max_output_tokens ? selected.max_output_tokens.toLocaleString() : "—"}`} /><Info label="上下文窗口" value={selected.context_window ? selected.context_window.toLocaleString() : "未记录"} /><Info label="能力标记" value={`${selected.supports_web_search ? "联网" : "无联网"} · ${selected.supports_images ? "图片" : "无图片"}`} />{selected.extra_body && <Info label="附加请求体" value={selected.extra_body} />}</dl>
            {tests[selected.id] && <div role="status" className={`mt-6 flex items-start gap-3 border-t pt-4 text-[13px] ${tests[selected.id].ok ? "border-emerald-400/30 text-emerald-300" : "border-red-400/30 text-red-300"}`}>{tests[selected.id].ok && <Check className="mt-0.5 size-4 shrink-0" />}<span>{tests[selected.id].ok ? "连接正常：" : "连接异常："}{tests[selected.id].detail}</span></div>}
            <div className="mt-9 flex flex-wrap items-center gap-x-6 gap-y-4 text-[12px]"><button type="button" onClick={() => startEdit(selected)} disabled={pending} className="text-white underline underline-offset-4 hover:text-white/65 disabled:opacity-40">编辑配置</button>{!selected.is_default && <button type="button" onClick={() => changeProvider(selected, "default")} disabled={pending} className="text-white/55 hover:text-white disabled:opacity-40">设为默认</button>}<button type="button" onClick={() => changeProvider(selected, "toggle")} disabled={pending} className="text-white/55 hover:text-white disabled:opacity-40">{selected.enabled ? "停用" : "启用"}</button><span className="flex-1" />{confirmingId === selected.id ? <span className="flex items-center gap-3 text-red-300"><span>确定删除？</span><button type="button" onClick={() => changeProvider(selected, "delete")} disabled={pending} className="underline underline-offset-4">确认</button><button type="button" onClick={() => setConfirmingId(null)} className="text-white/45">取消</button></span> : <button type="button" onClick={() => setConfirmingId(selected.id)} disabled={pending} className="text-white/35 hover:text-red-300 disabled:opacity-40">删除连接</button>}</div>
          </section> : <div className="border-y border-white/[0.1] py-14"><p className="max-w-[42ch] text-[16px] leading-7 text-white/50">连接建立后，这里会显示接口、模型与连通状态。点击“接入新模型”开始。</p></div>}
        </main>
      </div>

      <WeeklyReminder />

      <section className="mt-20 border-t border-white/[0.14] pt-7"><button type="button" onClick={() => { if (!ledgerOpen && calls === null) void onLoadCalls(); setLedgerOpen(!ledgerOpen); }} aria-expanded={ledgerOpen} className="flex w-full items-center justify-between gap-5 text-left"><span><span className="block text-[21px] font-medium">调用记账</span><span className="mt-2 block text-[12px] text-white/40">按周汇总与最近 50 条本地调用流水</span></span><ChevronDown className={`size-5 text-white/50 transition-transform ${ledgerOpen ? "rotate-180" : ""}`} /></button>{ledgerOpen && <div className="mt-8"><div className="mb-5 flex justify-end"><button type="button" onClick={onLoadCalls} disabled={callsPending} className="inline-flex items-center gap-2 text-[12px] text-white/55 hover:text-white disabled:opacity-40"><RefreshCw className={`size-3.5 ${callsPending ? "animate-spin" : ""}`} />{callsPending ? "读取中…" : "刷新流水"}</button></div>{callsError && <p role="alert" className="mb-5 text-[12px] text-red-300">{callsError}</p>}{callsPending && !calls && <p role="status" className="py-8 text-[12px] text-white/40">正在读取调用账…</p>}{calls && <>{calls.by_week.length === 0 ? <p className="py-8 text-[13px] text-white/40">暂无调用流水。模型实际被使用后，这里才会出现消耗记录。</p> : <><div className="overflow-x-auto"><table className="w-full min-w-[650px] border-collapse text-left text-[12px]"><thead className="border-b border-white/[0.15] text-white/40"><tr><th className="py-3 font-normal">周期</th><th className="py-3 font-normal">调用</th><th className="py-3 font-normal">成功 / 失败</th><th className="py-3 font-normal">输入 / 输出 tokens</th><th className="py-3 font-normal">总耗时</th></tr></thead><tbody className="divide-y divide-white/[0.08]">{calls.by_week.map((bucket) => <tr key={bucket.week} className="text-white/70"><td className="py-4 font-mono">{bucket.week}</td><td>{bucket.calls} 次</td><td>{bucket.ok} / {bucket.failed}</td><td className="font-mono">{bucket.input_tokens} / {bucket.output_tokens}</td><td>{(bucket.duration_ms / 1000).toFixed(1)} 秒</td></tr>)}</tbody></table></div><h3 className="mb-3 mt-10 text-[14px] font-medium text-white/75">最近流水</h3><div className="max-h-[400px] overflow-auto"><table className="w-full min-w-[750px] border-collapse text-left text-[11px]"><thead className="sticky top-0 bg-surface text-white/40"><tr>{["时间", "服务商", "模型", "业务任务", "tokens 入 / 出", "耗时", "状态"].map((label) => <th key={label} className="border-b border-white/[0.15] py-3 pr-4 font-normal">{label}</th>)}</tr></thead><tbody className="divide-y divide-white/[0.08]">{calls.calls.map((call) => <tr key={call.id} className="text-white/60"><td className="py-3 pr-4 font-mono whitespace-nowrap">{call.created_at.slice(0, 16).replace("T", " ")}</td><td className="pr-4">{providerLabel(call.provider_id)}</td><td className="pr-4">{call.model}</td><td className="pr-4">{call.task}</td><td className="pr-4 font-mono">{call.input_tokens ?? 0} / {call.output_tokens ?? 0}</td><td className="pr-4 font-mono">{call.duration_ms} ms</td><td className={call.ok ? "text-emerald-300" : "text-red-300"}>{call.ok ? "成功" : "失败"}</td></tr>)}</tbody></table></div></>}</>}</div>}</section>
    </div>
  );
}

function Field({ label, id, children }: { label: string; id: string; children: React.ReactNode }) { return <div><label htmlFor={id} className="mb-2 block text-[12px] text-white/55">{label}</label>{children}</div>; }
function Info({ label, value }: { label: string; value: string }) { return <div className="min-w-0 border-b border-white/[0.07] py-5"><dt className="mb-2 text-[11px] text-white/35">{label}</dt><dd className="break-all text-[14px] leading-6 text-white/75">{value}</dd></div>; }
