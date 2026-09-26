import { useEffect, useState } from "react";
import {
  Eye, EyeOff, Check, AlertTriangle, Loader2, Zap, Globe, Key, Cpu, Settings,
  ShieldCheck, Search, Lock, Trash2,
} from "lucide-react";
import { fetchConfig, saveConfig, testConfig, normalizeUrl, validateKey, fetchModels, resetMyData } from "../api/client";

/** 与后端 RESET_CONFIRM_TEXT 保持一致 —— 两边都校验，避免只改一侧。 */
const RESET_CONFIRM_TEXT = "清空";

/**
 * 快捷预设。
 * vision 表示该模型是否支持图片输入——批改照片/扫描件依赖这个能力，纯文本模型会直接报错。
 * 模型名会随厂商迭代变化，保存前请用「检测模型」按实际可用列表核对。
 */
const presets = [
  { name: "通义千问", endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen3.6-plus",       vision: true,  hint: "支持图片输入，可拍照批改" },
  { name: "DeepSeek", endpoint: "https://api.deepseek.com/v1",                       model: "deepseek-flash",     vision: true,  hint: "支持图片输入，可拍照批改" },
  { name: "智谱清言",  endpoint: "https://open.bigmodel.cn/api/paas/v4",              model: "glm-5v-turbo",       vision: true,  hint: "视觉版；GLM-5 / GLM-5.1 是纯文本，读不了图" },
  { name: "Moonshot", endpoint: "https://api.moonshot.cn/v1",                        model: "moonshot-v1-auto",   vision: false, hint: "纯文本系列，拍照请先确认型号支持图片" },
];

const FALLBACK_SENTINEL = "__KEEP_EXISTING__";

export default function SettingsPage() {
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey]     = useState("");     // 只承载用户新输入的明文
  const [model, setModel]       = useState("");
  const [showKey, setShowKey]   = useState(false);
  const [configured, setConfigured] = useState(false);
  const [saving, setSaving]     = useState(false);
  const [testing, setTesting]   = useState(false);
  const [toast, setToast]       = useState<{ ok: boolean; msg: string } | null>(null);

  // 「清空我的数据」两步确认：先展开确认区，输入「清空」后按钮才可点
  const [resetOpen, setResetOpen]   = useState(false);
  const [resetText, setResetText]   = useState("");
  const [resetting, setResetting]   = useState(false);
  const [resetMsg, setResetMsg]     = useState("");
  const [resetMsgOk, setResetMsgOk] = useState(false);

  // 服务端从不回传明文 Key，只给掩码 + 是否已存在
  const [hasKey, setHasKey]           = useState(false);
  const [keyMasked, setKeyMasked]     = useState("");
  const [maskSentinel, setMaskSentinel] = useState(FALLBACK_SENTINEL);

  const [validatingKey, setValidatingKey] = useState(false);
  const [keyStatus, setKeyStatus] = useState<{ valid: boolean; message: string } | null>(null);

  const [modelList, setModelList] = useState<{ id: string; name?: string }[]>([]);
  const [detectingModels, setDetectingModels] = useState(false);
  const [modelDetectMsg, setModelDetectMsg] = useState("");

  useEffect(() => {
    fetchConfig().then((c) => {
      setEndpoint(c.endpoint ?? "");
      setModel(c.model_name ?? "");
      setHasKey(c.has_api_key ?? false);
      setKeyMasked(c.api_key_masked ?? "");
      setMaskSentinel(c.mask_sentinel ?? FALLBACK_SENTINEL);
      setConfigured(c.is_configured ?? false);
    }).catch(() => {});
  }, []);

  const flash = (ok: boolean, msg: string) => { setToast({ ok, msg }); setTimeout(() => setToast(null), 3500); };

  /** 用户没改 Key 就传哨兵值，让后端沿用已保存的 Key */
  const keyPayload = () => apiKey.trim() || (hasKey ? maskSentinel : "");

  const handleSave = async () => {
    if (!endpoint || !model) { flash(false, "请填写端点与模型名称"); return; }
    const key = keyPayload();
    if (!key) { flash(false, "请填写 API Key"); return; }

    setSaving(true);
    try {
      const r = await saveConfig({ endpoint, api_key: key, model_name: model });
      setConfigured(true);
      if (apiKey.trim()) {
        setApiKey("");
        setKeyMasked(apiKey.trim().length > 8
          ? apiKey.trim().slice(0, 4) + "****" + apiKey.trim().slice(-4)
          : "****");
        setHasKey(true);
      }
      flash(true, r.message);
    } catch (e: any) {
      flash(false, e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try { const r = await testConfig(); flash(r.success, r.message); }
    catch (e: any) { flash(false, e.message); }
    finally { setTesting(false); }
  };

  const handleNormalizeUrl = async () => {
    if (!endpoint.trim()) return;
    try {
      const r = await normalizeUrl(endpoint);
      setEndpoint(r.url);
    } catch {
      // 静默失败，用户仍可手动修改
    }
  };

  const handleValidateKey = async () => {
    const key = keyPayload();
    if (!endpoint || !key) { flash(false, "请先填写 API 端点和密钥"); return; }
    setValidatingKey(true);
    setKeyStatus(null);
    try {
      const r = await validateKey(endpoint, key);
      setKeyStatus(r);
    } catch (e: any) {
      setKeyStatus({ valid: false, message: e.message || "验证失败" });
    } finally {
      setValidatingKey(false);
    }
  };

  const handleDetectModels = async () => {
    const key = keyPayload();
    if (!endpoint || !key) { flash(false, "请先填写 API 端点和密钥"); return; }
    setDetectingModels(true);
    setModelDetectMsg("");
    try {
      const r = await fetchModels(endpoint, key);
      if (r.models && r.models.length > 0) {
        setModelList(r.models);
        setModelDetectMsg(`检测到 ${r.models.length} 个可用模型`);
        if (!model) setModel(r.models[0].id);
      } else {
        setModelList([]);
        setModelDetectMsg(r.message || "未检测到可用模型，请手动输入");
      }
    } catch (e: any) {
      setModelList([]);
      setModelDetectMsg(e.message || "检测失败，请手动输入模型名称");
    } finally {
      setDetectingModels(false);
    }
  };

  const handleResetData = async () => {
    setResetting(true);
    setResetMsg("");
    try {
      const r = await resetMyData(RESET_CONFIRM_TEXT);
      setResetMsgOk(true);
      setResetMsg(r.message + (r.removed_files > 0 ? `，同时删除 ${r.removed_files} 个导出文件` : ""));
      setResetOpen(false);
      setResetText("");
    } catch (e: any) {
      setResetMsgOk(false);
      setResetMsg(e.message || "清空失败，请重试");
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="page-container settings-page">
      <div className="page-title">
        <div>
          <h2><Settings size={20} style={{ color: "var(--coral)" }} /> API 配置</h2>
          <p>配置兼容 OpenAI 格式的多模态大模型 API</p>
        </div>
        <span className={configured ? "badge-ok" : "badge-warn"}>{configured ? "✓ 已配置" : "未配置"}</span>
      </div>

      <div className="preset-group">
        <p>快捷预设</p>
        <div className="preset-btns">
          {presets.map((p) => (
            <button
              key={p.name}
              className="preset-btn"
              title={p.hint}
              onClick={() => { setEndpoint(p.endpoint); setModel(p.model); setKeyStatus(null); setModelList([]); setModelDetectMsg(""); }}
            >
              {p.name}
              {p.vision ? "" : "（纯文本）"}
            </button>
          ))}
        </div>
        <div className="config-tip" style={{ marginTop: 10 }}>
          <p><strong>模型必须支持图片输入</strong>，否则上传照片或扫描件会直接报错。</p>
          <p>纯文本模型只能用于「文字输入」标签和文字版 PDF。不确定时请点「检测模型」查看真实可用列表。</p>
        </div>
      </div>

      <div className="card">
        {/* ── API 端点 ── */}
        <div className="form-field">
          <label><Globe size={14} style={{ color: "var(--text-3)" }} /> API 端点</label>
          <input
            className="form-input"
            value={endpoint}
            onChange={e => setEndpoint(e.target.value)}
            onBlur={handleNormalizeUrl}
            placeholder="https://api.openai.com/v1"
          />
          <div className="config-tip">
            <p>正确格式示例：<code>https://api.deepseek.com/v1</code></p>
            <p>URL 应以 <code>/v1</code> 结尾，如果缺少会自动补全</p>
          </div>
        </div>

        {/* ── API 密钥 ── */}
        <div className="form-field">
          <label><Key size={14} style={{ color: "var(--text-3)" }} /> API 密钥</label>
          <div style={{ position: "relative", display: "flex", gap: 8, alignItems: "center" }}>
            <div style={{ position: "relative", flex: 1 }}>
              <input
                className="form-input"
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={e => { setApiKey(e.target.value); setKeyStatus(null); }}
                placeholder={hasKey ? `已保存：${keyMasked}（留空则沿用）` : "sk-..."}
                style={{ paddingRight: 36 }}
              />
              <button
                onClick={() => setShowKey(!showKey)}
                aria-label={showKey ? "隐藏密钥" : "显示密钥"}
                style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: "var(--text-3)", cursor: "pointer" }}
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
            <button className="btn-secondary" onClick={handleValidateKey} disabled={validatingKey} style={{ whiteSpace: "nowrap", padding: "10px 14px" }}>
              {validatingKey ? <Loader2 size={14} className="anim-spin" /> : <ShieldCheck size={14} />} 验证
            </button>
          </div>
          {hasKey && !apiKey && (
            <div className="config-tip" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Lock size={12} /> 密钥仅保存在服务器，不会回传到浏览器。留空即表示沿用已保存的密钥。
            </div>
          )}
          {keyStatus && (
            <div className={`key-status ${keyStatus.valid ? "valid" : "invalid"}`}>
              {keyStatus.valid ? "✓" : "✗"} {keyStatus.message}
            </div>
          )}
        </div>

        {/* ── 模型选择 ── */}
        <div className="form-field">
          <label><Cpu size={14} style={{ color: "var(--text-3)" }} /> 模型名称</label>
          <div className="model-selector">
            <div style={{ position: "relative", flex: 1 }}>
              {modelList.length > 0 ? (
                <select
                  className="form-select"
                  value={modelList.some(m => m.id === model) ? model : "__custom__"}
                  onChange={e => {
                    if (e.target.value === "__custom__") return;
                    setModel(e.target.value);
                  }}
                >
                  {modelList.map(m => (
                    <option key={m.id} value={m.id}>{m.name || m.id}</option>
                  ))}
                  {!modelList.some(m => m.id === model) && model && (
                    <option value="__custom__">{model}（手动输入）</option>
                  )}
                </select>
              ) : (
                <input
                  className="form-input"
                  value={model}
                  onChange={e => setModel(e.target.value)}
                  placeholder="gpt-4o"
                />
              )}
            </div>
            <button className="btn-secondary" onClick={handleDetectModels} disabled={detectingModels} style={{ whiteSpace: "nowrap", padding: "10px 14px" }}>
              {detectingModels ? <Loader2 size={14} className="anim-spin" /> : <Search size={14} />} 检测模型
            </button>
          </div>
          {modelList.length > 0 && (
            <input
              className="form-input"
              value={model}
              onChange={e => setModel(e.target.value)}
              placeholder="或手动输入模型名称"
              style={{ marginTop: 8 }}
            />
          )}
          {modelDetectMsg && (
            <div className="config-tip" style={{ marginTop: 6 }}>
              <p>{modelDetectMsg}</p>
            </div>
          )}
        </div>

        <div className="btn-row">
          <button className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 size={14} className="anim-spin" /> : <Check size={14} />} 保存配置
          </button>
          <button className="btn-secondary" onClick={handleTest} disabled={testing || !configured}>
            {testing ? <Loader2 size={14} className="anim-spin" /> : <Zap size={14} />} 测试连接
          </button>
        </div>
      </div>

      {/* ── 数据管理：清空本账号的批改记录 / 错题 / 练习 ── */}
      <div className="card danger-card">
        <h3 className="danger-title">
          <Trash2 size={15} style={{ color: "var(--coral)" }} /> 数据管理
        </h3>
        <p className="danger-desc">
          清空本账号的<strong>批改记录、错题和练习</strong>，导出的 PDF 一并删除。
          学生档案和登录账号会保留 —— 清完就是「有学生、但还没批改过作业」的状态。
        </p>

        {!resetOpen ? (
          <button className="btn-danger" onClick={() => { setResetOpen(true); setResetText(""); setResetMsg(""); }}>
            <Trash2 size={14} /> 清空我的数据
          </button>
        ) : (
          <div className="danger-confirm">
            <p className="danger-warn">
              <AlertTriangle size={14} /> 此操作不可恢复。请输入「{RESET_CONFIRM_TEXT}」两个字确认。
            </p>
            <input
              className="form-input"
              value={resetText}
              onChange={e => setResetText(e.target.value)}
              placeholder={RESET_CONFIRM_TEXT}
            />
            <div className="btn-row">
              <button
                className="btn-danger"
                disabled={resetText.trim() !== RESET_CONFIRM_TEXT || resetting}
                onClick={handleResetData}
              >
                {resetting ? <Loader2 size={14} className="anim-spin" /> : <Trash2 size={14} />} 确认清空
              </button>
              <button className="btn-secondary" onClick={() => { setResetOpen(false); setResetText(""); }}>
                取消
              </button>
            </div>
          </div>
        )}

        {resetMsg && (
          <div className={`key-status ${resetMsgOk ? "valid" : "invalid"}`}>{resetMsg}</div>
        )}
      </div>

      {toast && (
        <div className={`toast ${toast.ok ? "ok" : "err"}`}>
          {toast.ok ? <Check size={14} /> : <AlertTriangle size={14} />} {toast.msg}
        </div>
      )}
    </div>
  );
}
