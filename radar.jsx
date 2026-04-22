import { useState, useMemo, useEffect, useRef } from "react";
import {
  Search, RefreshCw, ChevronDown, X, ChevronUp,
  Bot, ArrowUpRight, User, Clock, Flame, Wind, Snowflake,
  Building2, Star, Rocket, HelpCircle, Filter, Calendar,
  TrendingUp, Users, AlertCircle, CheckCircle, PhoneCall,
  MessageSquare, BarChart2, Zap
} from "lucide-react";

// ─── Mock Data ────────────────────────────────────────────────────────────────
const LEADS = [
  {
    id: 1, hora: "08:14", nome: "Fernanda Lima", empresa: "Bradesco Seguros",
    rd_deal_id: "69e8c8266e1b130013565a1a",
    empresa_tier: "estrategica", telefone: "11 98765-4321",
    tema: "Liderança e Gestão", tipo: "In Company", formato: "Presencial",
    temp: "quente", score: 92, status: "Em atendimento humano",
    proximo_passo: "Enviar proposta", quem: "consultor", sla_min: 8,
    trail: "B", qtd: 40, cidade: "São Paulo",
    resumo: "Empresa busca programa de liderança para 40 gestores. Prazo: março.",
    mensagens: ["Boa tarde, preciso de um treinamento para nossa liderança", "São 40 pessoas, gestores de média gerência", "Preciso até março, pode ser?"],
    dados: { cargo: "Gerente de RH", email: "fernanda@bradesco.com.br", prazo: "Março 2026", objetivo: "Desenvolver competências de liderança" },
    ia_rec: "Lead quente com alto score. Enviar proposta customizada imediatamente. Trail B confirmada.",
    historico: ["Novo", "Em qualificação", "Transferido", "Em atendimento humano"],
  },
  {
    id: 2, hora: "08:31", nome: "Carlos Mendes", empresa: "Totvs S/A",
    empresa_tier: "alto_potencial", telefone: "11 91234-5678",
    tema: "Excel Avançado", tipo: "Turma Fechada", formato: "Online",
    temp: "quente", score: 85, status: "Transferido",
    proximo_passo: "Consultor deve assumir", quem: "transferido", sla_min: 22,
    trail: "B", qtd: 15, cidade: "Barueri",
    resumo: "Time de análise financeira precisa de Excel avançado. 15 pessoas, formato online.",
    mensagens: ["Olá, quero treinamento de Excel para meu time", "São 15 pessoas do financeiro", "Online está ótimo"],
    dados: { cargo: "Coordenador Financeiro", email: "carlos@totvs.com.br", prazo: "Sem data definida", objetivo: "Capacitar time em Excel avançado" },
    ia_rec: "Lead qualificado aguardando consultor. Contato urgente recomendado — SLA excedido.",
    historico: ["Novo", "Em qualificação", "Transferido"],
  },
  {
    id: 3, hora: "09:02", nome: "Ana Paula Rocha", empresa: "Startup Nova",
    empresa_tier: "comum", telefone: "21 99876-5432",
    tema: "Python para Dados", tipo: "Turma Aberta", formato: "Online",
    temp: "morno", score: 68, status: "Em qualificação",
    proximo_passo: "Coletar número de participantes", quem: "bot", sla_min: 5,
    trail: "A", qtd: null, cidade: "Rio de Janeiro",
    resumo: "Interesse em curso de Python. Ainda explorando opções de turma aberta.",
    mensagens: ["Tenho interesse em Python", "Qual a grade de vocês?"],
    dados: { cargo: "Analista de Dados", email: "", prazo: "Flexível", objetivo: "Aprender Python para análise de dados" },
    ia_rec: "Lead morno em qualificação. Bot coletando dados. Acompanhar progresso.",
    historico: ["Novo", "Em qualificação"],
  },
  {
    id: 4, hora: "09:18", nome: "Roberto Faria", empresa: "Ambev",
    empresa_tier: "estrategica", telefone: "11 93456-7890",
    tema: "Locação de Espaço", tipo: "Locação", formato: "Presencial",
    temp: "quente", score: 78, status: "Em atendimento humano",
    proximo_passo: "Validar disponibilidade de datas", quem: "consultor", sla_min: 3,
    trail: "D", qtd: 80, cidade: "São Paulo",
    resumo: "Precisa de espaço para evento corporativo de 80 pessoas. Data: próximo mês.",
    mensagens: ["Preciso alugar um espaço para evento", "80 pessoas, próximo mês", "Tem disponibilidade?"],
    dados: { cargo: "Eventos Corporativos", email: "roberto@ambev.com.br", prazo: "Próximo mês", objetivo: "Espaço para evento anual da diretoria" },
    ia_rec: "Locação urgente. Trail D confirmada. Consultor deve verificar agenda e fechar.",
    historico: ["Novo", "Transferido", "Em atendimento humano"],
  },
  {
    id: 5, hora: "09:45", nome: "Juliana Costa", empresa: "Magazine Luiza",
    empresa_tier: "alto_potencial", telefone: "11 97654-3210",
    tema: "Negociação e Vendas", tipo: "In Company", formato: "Presencial",
    temp: "morno", score: 71, status: "Qualificado",
    proximo_passo: "Aguardar retorno do lead", quem: "transferido", sla_min: 45,
    trail: "B", qtd: 25, cidade: "São Paulo",
    resumo: "Time de vendas precisa de capacitação em negociação. 25 pessoas.",
    mensagens: ["Quero um treinamento de vendas", "Time de 25 pessoas", "Vou confirmar com minha diretora"],
    dados: { cargo: "Supervisora Comercial", email: "juliana@magalu.com.br", prazo: "Q2 2026", objetivo: "Aumentar conversão da equipe de vendas" },
    ia_rec: "Lead qualificado aguardando aprovação interna. SLA crítico — follow-up necessário.",
    historico: ["Novo", "Em qualificação", "Transferido", "Qualificado"],
  },
  {
    id: 6, hora: "10:03", nome: "Marcos Oliveira", empresa: "TechBR Soluções",
    empresa_tier: "relevante", telefone: "11 95432-1098",
    tema: "Power BI", tipo: "Turma Fechada", formato: "Online",
    temp: "morno", score: 62, status: "Aguardando lead",
    proximo_passo: "Confirmar formato preferido", quem: "bot", sla_min: 12,
    trail: "B", qtd: 8, cidade: "Campinas",
    resumo: "Equipe de TI quer Power BI. 8 pessoas. Online ou presencial ainda indefinido.",
    mensagens: ["Power BI para minha equipe", "São 8 pessoas", "Pode ser online?"],
    dados: { cargo: "Diretor de TI", email: "marcos@techbr.com", prazo: "Fevereiro 2026", objetivo: "Dashboards gerenciais" },
    ia_rec: "Lead em coleta de dados. Confirmar formato e prazo para avançar.",
    historico: ["Novo", "Em qualificação", "Aguardando lead"],
  },
  {
    id: 7, hora: "10:29", nome: "Patricia Santos", empresa: "Consultoria XYZ",
    empresa_tier: "comum", telefone: "19 98765-0987",
    tema: "Gestão de Projetos", tipo: "Turma Aberta", formato: "Presencial",
    temp: "frio", score: 38, status: "Em qualificação",
    proximo_passo: "Coletar mais informações", quem: "bot", sla_min: 7,
    trail: "C", qtd: 2, cidade: "Campinas",
    resumo: "Interesse genérico em gestão de projetos. Pouco engajamento.",
    mensagens: ["Vocês têm curso de gestão de projetos?", "Qual o valor?"],
    dados: { cargo: "Consultora", email: "", prazo: "Sem prazo", objetivo: "Certificação PMP" },
    ia_rec: "Lead frio. Trail C — consultoria. Bot tentando qualificar interesse.",
    historico: ["Novo", "Em qualificação"],
  },
  {
    id: 8, hora: "10:47", nome: "Bruno Alves", empresa: "Raízen Energia",
    empresa_tier: "estrategica", telefone: "11 92109-8765",
    tema: "Compliance e ESG", tipo: "Customizado", formato: "Híbrido",
    temp: "quente", score: 88, status: "Transferido",
    proximo_passo: "Consultor deve assumir", quem: "transferido", sla_min: 31,
    trail: "B", qtd: 60, cidade: "São Paulo",
    resumo: "Programa customizado de Compliance para 60 colaboradores. Formato híbrido. Urgente.",
    mensagens: ["Preciso de um programa de compliance", "60 colaboradores no total", "Preciso de algo customizado para nossa realidade"],
    dados: { cargo: "Gerente de Compliance", email: "bruno@raizen.com.br", prazo: "Janeiro 2026", objetivo: "Compliance ESG para auditoria" },
    ia_rec: "Lead estratégico com urgência. SLA excedido. Consultor deve entrar imediatamente.",
    historico: ["Novo", "Em qualificação", "Transferido"],
  },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────
const getTempConfig = (temp) => ({
  quente: { label: "Quente", icon: <Flame size={11} />, cls: "bg-red-50 text-red-600 border-red-200" },
  morno:  { label: "Morno",  icon: <Wind size={11} />,  cls: "bg-amber-50 text-amber-600 border-amber-200" },
  frio:   { label: "Frio",   icon: <Snowflake size={11} />, cls: "bg-blue-50 text-blue-600 border-blue-200" },
}[temp] || { label: temp, icon: null, cls: "bg-gray-100 text-gray-500" });

const getStatusConfig = (s) => ({
  "Novo":                  "bg-slate-100 text-slate-600",
  "Em qualificação":       "bg-violet-50 text-violet-600",
  "Aguardando lead":       "bg-yellow-50 text-yellow-700",
  "Transferido":           "bg-orange-50 text-orange-600",
  "Em atendimento humano": "bg-blue-50 text-blue-700",
  "Qualificado":           "bg-emerald-50 text-emerald-700",
  "Concluído":             "bg-green-50 text-green-700",
  "Perdido":               "bg-red-50 text-red-500",
}[s] || "bg-gray-100 text-gray-500");

const getTipoConfig = (t) => ({
  "Turma Aberta":  "bg-sky-50 text-sky-700",
  "Turma Fechada": "bg-indigo-50 text-indigo-700",
  "In Company":    "bg-purple-50 text-purple-700",
  "Customizado":   "bg-pink-50 text-pink-700",
  "Locação":       "bg-teal-50 text-teal-700",
  "Não definido":  "bg-gray-100 text-gray-500",
}[t] || "bg-gray-100 text-gray-500");

const getFormatoConfig = (f) => ({
  "Presencial":    "bg-green-50 text-green-700",
  "Online":        "bg-blue-50 text-blue-600",
  "Híbrido":       "bg-purple-50 text-purple-700",
  "Não informado": "bg-gray-100 text-gray-500",
}[f] || "bg-gray-100 text-gray-500");

const getTierConfig = (tier) => ({
  estrategica:    { label: "Estratégica",    icon: <Star size={9} />,      cls: "bg-amber-50 text-amber-700 border-amber-200" },
  alto_potencial: { label: "Alto potencial", icon: <Rocket size={9} />,    cls: "bg-violet-50 text-violet-700 border-violet-200" },
  relevante:      { label: "Relevante",      icon: <Building2 size={9} />, cls: "bg-blue-50 text-blue-700 border-blue-200" },
  comum:          { label: "",               icon: null,                    cls: "" },
}[tier] || { label: "", icon: null, cls: "" });

const getScoreColor = (s) => s >= 80 ? "text-emerald-600" : s >= 60 ? "text-amber-600" : s >= 40 ? "text-orange-500" : "text-red-500";
const getScoreBg    = (s) => s >= 80 ? "bg-emerald-500"   : s >= 60 ? "bg-amber-400"   : s >= 40 ? "bg-orange-400"   : "bg-red-400";

const getSlaConfig = (min) => {
  if (min <= 10) return { cls: "text-emerald-700", bg: "bg-emerald-50", label: `${min}m` };
  if (min <= 25) return { cls: "text-amber-700",   bg: "bg-amber-50",   label: `${min}m` };
  return             { cls: "text-red-700",     bg: "bg-red-50",     label: `${min}m ⚠` };
};

const QuemIcon = ({ quem }) => {
  const cfg = {
    bot:         { icon: <Bot size={14} />,          label: "Bot SDR",           cls: "text-violet-500 bg-violet-50" },
    transferido: { icon: <ArrowUpRight size={14} />, label: "Aguard. Consultor", cls: "text-orange-500 bg-orange-50" },
    consultor:   { icon: <User size={14} />,         label: "Consultor",         cls: "text-blue-600 bg-blue-50" },
  }[quem] || { icon: <HelpCircle size={14} />, label: "—", cls: "text-gray-400 bg-gray-100" };
  return (
    <div className="relative group flex items-center justify-center">
      <span className={`flex items-center justify-center w-7 h-7 rounded-full ${cfg.cls}`}>
        {cfg.icon}
      </span>
      <div className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 z-20 whitespace-nowrap bg-gray-900 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        {cfg.label}
      </div>
    </div>
  );
};

// ─── Badge ────────────────────────────────────────────────────────────────────
const Badge = ({ children, className = "" }) => (
  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${className}`}>
    {children}
  </span>
);

// ─── KPI Card ─────────────────────────────────────────────────────────────────
const KpiCard = ({ icon, label, value, color = "text-slate-700", urgent = false }) => (
  <div className={`bg-white rounded-xl px-4 py-3 flex items-center gap-3 shadow-sm border ${urgent ? "border-red-200 bg-red-50/60" : "border-slate-100"}`}>
    <div className={`flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${urgent ? "bg-red-100 text-red-500" : "bg-slate-100 text-slate-500"}`}>
      {icon}
    </div>
    <div>
      <div className={`text-xl font-bold leading-none ${urgent ? "text-red-600" : color}`}>{value}</div>
      <div className="text-xs text-slate-400 mt-0.5 whitespace-nowrap">{label}</div>
    </div>
  </div>
);

// ─── Conversa Tallos ──────────────────────────────────────────────────────────
const TallosConversation = ({ phone }) => {
  const [msgs, setMsgs]       = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");
  const [page, setPage]       = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const bottomRef             = useRef(null);

  const fetchHistory = async (p = 1, append = false) => {
    setLoading(true);
    setError("");
    try {
      const res  = await fetch(`/pj/radar/tallos-history/${phone}?page=${p}&limit=50`);
      const data = await res.json();
      if (data.error && !data.messages?.length) {
        setError(data.error);
      } else {
        setMsgs(prev => append ? [...prev, ...data.messages] : data.messages);
        setHasMore(data.has_more || false);
        setPage(p);
      }
    } catch (e) {
      setError("Erro ao carregar conversa.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchHistory(1); }, [phone]);

  useEffect(() => {
    if (!loading && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [msgs, loading]);

  if (loading && msgs.length === 0) return (
    <div className="flex items-center gap-2 text-slate-400 text-xs py-4 justify-center">
      <RefreshCw size={12} className="animate-spin" /> Carregando conversa...
    </div>
  );

  if (error && msgs.length === 0) return (
    <div className="bg-amber-50 border border-amber-100 rounded-lg p-3 text-xs text-amber-700">
      {error === "contact_id Tallos não encontrado para este lead"
        ? "Histórico Tallos não disponível — contact_id ainda não registrado para este lead."
        : error}
    </div>
  );

  if (msgs.length === 0) return (
    <div className="text-xs text-slate-400 text-center py-4">Nenhuma mensagem encontrada.</div>
  );

  return (
    <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
      {hasMore && (
        <button
          onClick={() => fetchHistory(page + 1, true)}
          className="w-full text-xs text-indigo-600 hover:text-indigo-800 py-1 text-center"
          disabled={loading}
        >
          {loading ? "Carregando..." : "↑ Carregar mensagens anteriores"}
        </button>
      )}
      {msgs.map((m, i) => {
        const isOperator = m.role === "operator";
        const isBot      = m.role === "bot";
        const isCustomer = m.role === "customer";
        return (
          <div key={i} className={`flex gap-2 ${isCustomer ? "" : "flex-row-reverse"}`}>
            {/* Avatar */}
            <div className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5 ${
              isOperator ? "bg-blue-100" : isBot ? "bg-indigo-100" : "bg-slate-200"
            }`}>
              {isOperator ? <User size={10} className="text-blue-600" /> :
               isBot      ? <Bot  size={10} className="text-indigo-600" /> :
                            <User size={10} className="text-slate-500" />}
            </div>
            {/* Balão */}
            <div className={`flex flex-col max-w-[80%] ${isCustomer ? "" : "items-end"}`}>
              {(isOperator || isBot) && (
                <span className="text-[10px] text-slate-400 mb-0.5 px-1">
                  {isOperator ? (m.operator_name || "Operador") : "Bot"}
                </span>
              )}
              <div className={`rounded-xl px-3 py-2 text-xs ${
                isOperator ? "bg-blue-50 text-blue-900 rounded-tr-sm" :
                isBot      ? "bg-indigo-50 text-indigo-900 rounded-tr-sm" :
                             "bg-slate-50 text-slate-700 rounded-tl-sm"
              }`}>
                {m.message}
              </div>
              <span className="text-[10px] text-slate-400 mt-0.5 px-1">{m.hora}</span>
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
};

// ─── Drawer ───────────────────────────────────────────────────────────────────
const LeadDetailDrawer = ({ lead, onClose }) => {
  if (!lead) return null;
  const temp = getTempConfig(lead.temp);
  const tier = getTierConfig(lead.empresa_tier);
  const sla  = getSlaConfig(lead.sla_min);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/25 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-[420px] bg-white shadow-2xl flex flex-col h-full overflow-y-auto" style={{ animation: "slideIn .22s cubic-bezier(.16,1,.3,1)" }}>
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>

        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-slate-100 sticky top-0 bg-white z-10">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-bold text-slate-800">{lead.nome}</h2>
              {tier.label && (
                <Badge className={`${tier.cls} border text-xs`}>{tier.icon}{tier.label}</Badge>
              )}
            </div>
            <div className="text-sm text-slate-400 mt-0.5 flex items-center gap-1.5">
              <Building2 size={12} />{lead.empresa}
            </div>
          </div>
          <div className="flex items-center gap-1">
            {lead.rd_deal_id && (
              <a
                href={`https://crm.rdstation.com/app/deals/${lead.rd_deal_id}`}
                target="_blank"
                rel="noopener noreferrer"
                title="Ver deal no RD CRM"
                className="flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 border border-indigo-100 px-2.5 py-1.5 rounded-lg transition-colors"
              >
                <ArrowUpRight size={13} /> RD CRM
              </a>
            )}
            <button onClick={onClose} className="text-slate-400 hover:text-slate-700 p-1.5 rounded-lg hover:bg-slate-100 transition-colors ml-1">
              <X size={17} />
            </button>
          </div>
        </div>

        <div className="p-5 space-y-5">
          {/* Chips */}
          <div className="flex flex-wrap gap-2">
            <Badge className={`${temp.cls} border`}>{temp.icon}{temp.label}</Badge>
            <Badge className={getStatusConfig(lead.status)}>{lead.status}</Badge>
            <Badge className={`${sla.bg} ${sla.cls}`}><Clock size={10}/>{sla.label}</Badge>
            <Badge className="bg-indigo-50 text-indigo-700">Trail {lead.trail}</Badge>
            <Badge className={`${getScoreColor(lead.score)} bg-slate-50 font-bold`}>Score {lead.score}</Badge>
          </div>

          {/* Recomendação IA */}
          <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-4">
            <div className="flex items-center gap-1.5 text-indigo-700 font-semibold text-xs mb-2">
              <Zap size={12} /> Recomendação da IA
            </div>
            <p className="text-sm text-indigo-800">{lead.ia_rec}</p>
          </div>

          {/* Próximo passo */}
          <div className="bg-amber-50 border border-amber-100 rounded-xl p-4">
            <div className="flex items-center gap-1.5 text-amber-700 font-semibold text-xs mb-1">
              <CheckCircle size={12} /> Próximo Passo
            </div>
            <p className="text-sm text-amber-800 font-medium">{lead.proximo_passo}</p>
          </div>

          {/* Dados */}
          <div>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Dados Capturados</h3>
            <div className="grid grid-cols-2 gap-2">
              {[
                ["Telefone", lead.telefone], ["Canal", "WhatsApp"],
                ["Tema", lead.tema],         ["Tipo", lead.tipo],
                ["Formato", lead.formato],   ["Qtd.", lead.qtd || "—"],
                ["Cidade", lead.cidade],     ["Cargo", lead.dados.cargo || "—"],
                ["Email", lead.dados.email || "—"], ["Prazo", lead.dados.prazo],
              ].map(([k, v]) => (
                <div key={k} className="bg-slate-50 rounded-lg p-2.5">
                  <div className="text-xs text-slate-400">{k}</div>
                  <div className="font-medium text-slate-700 text-xs mt-0.5 truncate" title={v}>{v}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Conversa completa Tallos */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                Conversa
              </h3>
              <div className="flex items-center gap-2 text-[10px] text-slate-400">
                <span className="flex items-center gap-0.5"><span className="w-2 h-2 rounded-full bg-slate-200 inline-block"/> Lead</span>
                <span className="flex items-center gap-0.5"><span className="w-2 h-2 rounded-full bg-blue-200 inline-block"/> Operador</span>
                <span className="flex items-center gap-0.5"><span className="w-2 h-2 rounded-full bg-indigo-200 inline-block"/> Bot</span>
              </div>
            </div>
            <TallosConversation phone={lead.telefone} />
          </div>

          {/* Histórico */}
          <div>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Histórico de Status</h3>
            <div className="flex items-center flex-wrap gap-1">
              {lead.historico.map((s, i) => (
                <div key={i} className="flex items-center gap-1">
                  <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                    i === lead.historico.length - 1 ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500"
                  }`}>{s}</span>
                  {i < lead.historico.length - 1 && <div className="w-3 h-px bg-slate-300" />}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ─── Sort Header ─────────────────────────────────────────────────────────────
const SortIcon = ({ col, sortCol, sortDir }) => {
  if (sortCol !== col) return <ChevronDown size={11} className="text-slate-300 ml-0.5" />;
  return sortDir === "asc"
    ? <ChevronUp size={11} className="text-indigo-500 ml-0.5" />
    : <ChevronDown size={11} className="text-indigo-500 ml-0.5" />;
};

// ─── Main ─────────────────────────────────────────────────────────────────────
export default function Radar() {
  const [search, setSearch]     = useState("");
  const [selected, setSelected] = useState(null);
  const [sortCol, setSortCol]   = useState("hora");
  const [sortDir, setSortDir]   = useState("desc");
  const [filters, setFilters]   = useState({ temp: "", quem: "", score: "" });

  const kpis = useMemo(() => ({
    total:       LEADS.length,
    quentes:     LEADS.filter(l => l.temp === "quente").length,
    bot:         LEADS.filter(l => l.quem === "bot").length,
    transferido: LEADS.filter(l => l.quem === "transferido").length,
    humano:      LEADS.filter(l => l.quem === "consultor").length,
    parados:     LEADS.filter(l => l.sla_min > 25).length,
    qualificado: LEADS.filter(l => l.status === "Qualificado").length,
    locacao:     LEADS.filter(l => l.tipo === "Locação").length,
  }), []);

  const handleSort = (col) => {
    if (sortCol === col) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortCol(col); setSortDir("desc"); }
  };

  const setFilter = (key, val) =>
    setFilters(f => ({ ...f, [key]: f[key] === val ? "" : val }));

  const filtered = useMemo(() => {
    let data = [...LEADS];
    if (search) {
      const q = search.toLowerCase();
      data = data.filter(l =>
        l.nome.toLowerCase().includes(q) ||
        l.empresa.toLowerCase().includes(q) ||
        l.telefone.includes(q)
      );
    }
    if (filters.temp)  data = data.filter(l => l.temp === filters.temp);
    if (filters.quem)  data = data.filter(l => l.quem === filters.quem);
    if (filters.score === "alto")  data = data.filter(l => l.score >= 70);
    if (filters.score === "baixo") data = data.filter(l => l.score < 50);

    return [...data].sort((a, b) => {
      let av = a[sortCol] ?? "", bv = b[sortCol] ?? "";
      if (typeof av === "string") { av = av.toLowerCase(); bv = bv.toLowerCase(); }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ?  1 : -1;
      return 0;
    });
  }, [search, filters, sortCol, sortDir]);

  const activeFilters = Object.values(filters).filter(Boolean).length;

  const Th = ({ col, children }) => (
    <th
      onClick={() => col && handleSort(col)}
      className={`px-3 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wide whitespace-nowrap select-none ${col ? "cursor-pointer hover:text-slate-600" : ""}`}
    >
      <span className="flex items-center gap-0.5">
        {children}
        {col && <SortIcon col={col} sortCol={sortCol} sortDir={sortDir} />}
      </span>
    </th>
  );

  return (
    <div className="min-h-screen bg-slate-50 font-sans text-slate-900">
      {/* ── Header ── */}
      <header className="bg-white border-b border-slate-100 px-6 py-4 sticky top-0 z-30 shadow-sm">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 bg-indigo-600 rounded-lg flex items-center justify-center shadow-sm">
                <BarChart2 size={14} className="text-white" />
              </div>
              <h1 className="text-lg font-bold text-slate-800 tracking-tight">Radar</h1>
              <span className="flex items-center gap-1.5 bg-emerald-50 text-emerald-600 text-xs font-semibold px-2.5 py-1 rounded-full border border-emerald-100">
                <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" />
                Ao Vivo
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5 ml-9">Monitoramento em tempo real · Leads PJ · Treinamentos Corporativos</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Nome, empresa ou telefone…"
                className="pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-lg bg-slate-50 focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:bg-white w-60 transition-all"
              />
            </div>
            <button className="flex items-center gap-1.5 text-xs text-slate-500 bg-white border border-slate-200 px-3 py-2 rounded-lg hover:bg-slate-50 transition-colors">
              <Calendar size={13} /> Hoje <ChevronDown size={11} />
            </button>
            <button className="flex items-center gap-1.5 text-xs text-indigo-600 bg-indigo-50 border border-indigo-100 px-3 py-2 rounded-lg hover:bg-indigo-100 transition-colors font-semibold">
              <RefreshCw size={13} /> Atualizar
            </button>
          </div>
        </div>
      </header>

      <div className="px-6 py-5 space-y-4">
        {/* ── KPIs ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
          <KpiCard icon={<Users size={16}/>}        label="Leads hoje"    value={kpis.total}       color="text-indigo-600" />
          <KpiCard icon={<Flame size={16}/>}         label="Quentes"       value={kpis.quentes}     color="text-red-500" />
          <KpiCard icon={<Bot size={16}/>}           label="Com o bot"     value={kpis.bot}         color="text-violet-600" />
          <KpiCard icon={<ArrowUpRight size={16}/>}  label="Transferidos"  value={kpis.transferido} color="text-orange-500" />
          <KpiCard icon={<PhoneCall size={16}/>}     label="Com consultor" value={kpis.humano}      color="text-blue-600" />
          <KpiCard icon={<AlertCircle size={16}/>}   label="SLA crítico"   value={kpis.parados}     urgent={kpis.parados > 0} />
          <KpiCard icon={<CheckCircle size={16}/>}   label="Qualificados"  value={kpis.qualificado} color="text-emerald-600" />
          <KpiCard icon={<Building2 size={16}/>}     label="Locação"       value={kpis.locacao}     color="text-teal-600" />
        </div>

        {/* ── Filtros ── */}
        <div className="bg-white rounded-xl border border-slate-100 px-4 py-3 shadow-sm">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="flex items-center gap-1.5 text-xs text-slate-400 font-medium">
              <Filter size={12} /> Filtros
              {activeFilters > 0 && (
                <span className="w-4 h-4 bg-indigo-500 text-white rounded-full text-xs font-bold flex items-center justify-center">{activeFilters}</span>
              )}
            </span>
            <div className="w-px h-4 bg-slate-200" />

            {/* Temp */}
            {["quente","morno","frio"].map(t => {
              const cfg = getTempConfig(t);
              const on  = filters.temp === t;
              return (
                <button key={t} onClick={() => setFilter("temp", t)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                    on ? `${cfg.cls} border-current shadow-sm` : "bg-white text-slate-500 border-slate-200 hover:border-slate-300"
                  }`}>
                  {cfg.icon}{cfg.label}
                </button>
              );
            })}

            <div className="w-px h-4 bg-slate-200" />

            {/* Quem */}
            {[["bot","Bot"],["transferido","Transferido"],["consultor","Consultor"]].map(([v,l]) => (
              <button key={v} onClick={() => setFilter("quem", v)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                  filters.quem === v
                    ? "bg-indigo-50 text-indigo-700 border-indigo-200 shadow-sm"
                    : "bg-white text-slate-500 border-slate-200 hover:border-slate-300"
                }`}>{l}</button>
            ))}

            <div className="w-px h-4 bg-slate-200" />

            {/* Score */}
            {[["alto","Score 70+"],["baixo","Score <50"]].map(([v,l]) => (
              <button key={v} onClick={() => setFilter("score", v)}
                className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                  filters.score === v
                    ? "bg-slate-800 text-white border-slate-800 shadow-sm"
                    : "bg-white text-slate-500 border-slate-200 hover:border-slate-300"
                }`}>{l}</button>
            ))}

            {activeFilters > 0 && (
              <button onClick={() => setFilters({ temp:"", quem:"", score:"" })}
                className="ml-auto flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600 transition-colors">
                <X size={11} /> Limpar
              </button>
            )}
          </div>
        </div>

        {/* ── Tabela ── */}
        <div className="bg-white rounded-xl border border-slate-100 shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
            <span className="text-sm font-semibold text-slate-600">
              {filtered.length} lead{filtered.length !== 1 ? "s" : ""}
              {activeFilters > 0 && <span className="text-slate-400 font-normal"> filtrados</span>}
            </span>
            <span className="flex items-center gap-1.5 text-xs text-slate-400">
              <TrendingUp size={12} className="text-slate-300" />
              Clique em uma linha para ver detalhes
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-50/80 border-b border-slate-100">
                <tr>
                  <Th col="hora">Hora</Th>
                  <Th>Nome / Empresa</Th>
                  <Th>Telefone</Th>
                  <Th>Tema</Th>
                  <Th>Tipo</Th>
                  <Th>Formato</Th>
                  <Th col="temp">Temp.</Th>
                  <Th col="score">Score</Th>
                  <Th>Status</Th>
                  <Th>Próximo Passo</Th>
                  <Th col="quem">Quem</Th>
                  <Th col="sla_min">SLA</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={12} className="py-14 text-center">
                      <MessageSquare size={28} className="mx-auto mb-2 text-slate-200" />
                      <p className="text-sm text-slate-400">Nenhum lead encontrado com os filtros aplicados.</p>
                    </td>
                  </tr>
                ) : filtered.map(lead => {
                  const temp = getTempConfig(lead.temp);
                  const sla  = getSlaConfig(lead.sla_min);
                  const tier = getTierConfig(lead.empresa_tier);

                  return (
                    <tr
                      key={lead.id}
                      onClick={() => setSelected(lead)}
                      className={`cursor-pointer transition-colors hover:bg-indigo-50/40 ${
                        lead.sla_min > 25 ? "bg-red-50/20" : ""
                      } ${selected?.id === lead.id ? "bg-indigo-50/60" : ""}`}
                    >
                      {/* Hora */}
                      <td className="px-3 py-3.5">
                        <span className="font-mono text-xs text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                          {lead.hora}
                        </span>
                      </td>

                      {/* Nome / Empresa */}
                      <td className="px-3 py-3.5 min-w-[170px]">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-semibold text-slate-800 text-sm">{lead.nome}</span>
                          {tier.label && (
                            <Badge className={`${tier.cls} border text-xs`}>{tier.icon}{tier.label}</Badge>
                          )}
                        </div>
                        <div className="text-xs text-slate-400 mt-0.5 flex items-center gap-1">
                          <Building2 size={10}/>{lead.empresa}
                        </div>
                      </td>

                      {/* Telefone */}
                      <td className="px-3 py-3.5">
                        <span className="font-mono text-xs text-slate-500">{lead.telefone}</span>
                      </td>

                      {/* Tema */}
                      <td className="px-3 py-3.5 max-w-[130px]">
                        <span className="text-xs text-slate-700 font-medium truncate block" title={lead.tema}>{lead.tema}</span>
                      </td>

                      {/* Tipo */}
                      <td className="px-3 py-3.5">
                        <Badge className={getTipoConfig(lead.tipo)}>{lead.tipo}</Badge>
                      </td>

                      {/* Formato */}
                      <td className="px-3 py-3.5">
                        <Badge className={getFormatoConfig(lead.formato)}>{lead.formato}</Badge>
                      </td>

                      {/* Temperatura */}
                      <td className="px-3 py-3.5">
                        <Badge className={`${temp.cls} border`}>{temp.icon}{temp.label}</Badge>
                      </td>

                      {/* Score */}
                      <td className="px-3 py-3.5">
                        <div className="flex items-center gap-1.5">
                          <span className={`text-sm font-bold tabular-nums ${getScoreColor(lead.score)}`}>{lead.score}</span>
                          <div className="w-8 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${getScoreBg(lead.score)}`} style={{ width: `${lead.score}%` }} />
                          </div>
                        </div>
                      </td>

                      {/* Status */}
                      <td className="px-3 py-3.5 whitespace-nowrap">
                        <Badge className={getStatusConfig(lead.status)}>{lead.status}</Badge>
                      </td>

                      {/* Próximo Passo */}
                      <td className="px-3 py-3.5 max-w-[160px]">
                        <span className="text-xs text-slate-600 truncate block" title={lead.proximo_passo}>{lead.proximo_passo}</span>
                      </td>

                      {/* Quem */}
                      <td className="px-3 py-3.5">
                        <QuemIcon quem={lead.quem} />
                      </td>

                      {/* SLA */}
                      <td className="px-3 py-3.5 whitespace-nowrap">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${sla.bg} ${sla.cls}`}>
                          <Clock size={10}/>{sla.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {filtered.length > 0 && (
            <div className="px-4 py-2.5 border-t border-slate-100 flex items-center justify-between">
              <span className="text-xs text-slate-400">Atualizado agora mesmo</span>
              <span className="text-xs text-slate-400">
                {filtered.filter(l => l.temp === "quente").length} quentes ·{" "}
                {filtered.filter(l => l.sla_min > 25).length} SLA crítico
              </span>
            </div>
          )}
        </div>
      </div>

      {selected && <LeadDetailDrawer lead={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
