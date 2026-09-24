"""Application configuration loaded from environment and .env files.

Settings are loaded at import time and can be hot-reloaded at runtime via
``reload_settings()``. The module-level ``settings`` object is a proxy
that delegates attribute access to the current Settings instance, so all
callers automatically see the latest values after a reload.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

from dotenv import dotenv_values, load_dotenv  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDER_OPENROUTER = "openrouter"
PROVIDER_REQUESTY = "requesty"
PROVIDER_META = "meta"
PROVIDER_NANO_GPT = "nano-gpt"
PROVIDER_LLMTR = "llmtr"
PROVIDER_KENARI = "kenari"
PROVIDER_UNOROUTER = "unorouter"
PROVIDER_RUNINFRA = "runinfra"
PROVIDER_NVIDIA = "nvidia"
PROVIDER_OPENCODE = "opencode"
PROVIDER_ORCAROUTER = "orcarouter"
PROVIDER_AIHUBMIX = "aihubmix"
PROVIDER_TOKENROUTER = "tokenrouter"
PROVIDER_INFERX = "inferx"
PROVIDER_KILO = "kilo"
PROVIDER_PENDRA = "pendra"
PROVIDER_VERCEL = "vercel"
PROVIDER_ZENMUX = "zenmux"
PROVIDER_ABLITERATION_AI = "abliteration-ai"

PROVIDER_ABOVE = "above"

PROVIDER_AGENTROUTER = "agentrouter"

PROVIDER_AGNES = "agnes"

PROVIDER_AI_ROUTER = "ai-router"

PROVIDER_AIAND = "aiand"

PROVIDER_AINETCAFE = "ainetcafe"

PROVIDER_AIXY = "aixy"

PROVIDER_AKI_IO = "aki-io"

PROVIDER_ALIBABA = "alibaba"

PROVIDER_302AI = "302ai"

PROVIDER_ABACUS = "abacus"

PROVIDER_ALIBABA_CN = "alibaba-cn"

PROVIDER_ALIBABA_CODING_PLAN = "alibaba-coding-plan"

PROVIDER_ALIBABA_CODING_PLAN_CN = "alibaba-coding-plan-cn"

PROVIDER_ALIBABA_TOKEN_PLAN = "alibaba-token-plan"

PROVIDER_ALIBABA_TOKEN_PLAN_CN = "alibaba-token-plan-cn"

PROVIDER_AMAZON_BEDROCK = "amazon-bedrock"

PROVIDER_AMBIENT = "ambient"

PROVIDER_AMD = "amd"

PROVIDER_ANTHROPIC = "anthropic"

PROVIDER_ANYAPI = "anyapi"

PROVIDER_ARCEE = "arcee"

PROVIDER_ATOMIC_CHAT = "atomic-chat"

PROVIDER_AURIKO = "auriko"

PROVIDER_AZURE = "azure"

PROVIDER_AZURE_COGNITIVE_SERVICES = "azure-cognitive-services"

PROVIDER_BAILING = "bailing"

PROVIDER_BASETEN = "baseten"

PROVIDER_BERGET = "berget"

PROVIDER_BLUECLAW = "blueclaw"

PROVIDER_BOTHUB = "bothub"

PROVIDER_CEREBRAS = "cerebras"

PROVIDER_CHUTES = "chutes"

PROVIDER_CLARIFAI = "clarifai"

PROVIDER_CLAUDINIO = "claudinio"

PROVIDER_CLINE_PASS = "cline-pass"

PROVIDER_CLOUDFERRO_SHERLOCK = "cloudferro-sherlock"

PROVIDER_CLOUDFLARE_AI_GATEWAY = "cloudflare-ai-gateway"

PROVIDER_CLOUDFLARE_WORKERS_AI = "cloudflare-workers-ai"

PROVIDER_COHERE = "cohere"

PROVIDER_CORALBRICKS = "coralbricks"

PROVIDER_CORTECS = "cortecs"

PROVIDER_CROF = "crof"

PROVIDER_CROSSMODEL = "crossmodel"

PROVIDER_CRUSOE = "crusoe"

PROVIDER_DAOXE = "daoxe"

PROVIDER_DATABRICKS = "databricks"

PROVIDER_DEEPINFRA = "deepinfra"

PROVIDER_DEEPSEEK = "deepseek"

PROVIDER_DIGITALOCEAN = "digitalocean"

PROVIDER_DINFERENCE = "dinference"

PROVIDER_DRUN = "drun"

PROVIDER_EBCLOUD = "ebcloud"

PROVIDER_ECHO = "echo"

PROVIDER_EDENAI = "edenai"

PROVIDER_EMPIRIOLABS = "empiriolabs"

PROVIDER_EVROC = "evroc"

PROVIDER_FASTROUTER = "fastrouter"

PROVIDER_FIREWORKS_AI = "fireworks-ai"

PROVIDER_FREEMODEL = "freemodel"

PROVIDER_FRIENDLI = "friendli"

PROVIDER_FROGBOT = "frogbot"

PROVIDER_GITHUB_COPILOT = "github-copilot"

PROVIDER_GMICLOUD = "gmicloud"

PROVIDER_GOOGLE = "google"

PROVIDER_GOOGLE_VERTEX = "google-vertex"

PROVIDER_GOOGLE_VERTEX_ANTHROPIC = "google-vertex-anthropic"

PROVIDER_GREENPT = "greenpt"

PROVIDER_GROQ = "groq"

PROVIDER_HELICONE = "helicone"

PROVIDER_HETZNER = "hetzner"

PROVIDER_HPC_AI = "hpc-ai"

PROVIDER_HUGGINGFACE = "huggingface"

PROVIDER_HYPER = "hyper"

PROVIDER_IFLOWCN = "iflowcn"

PROVIDER_IMPOSSIBL = "impossibl"

PROVIDER_INCEPTION = "inception"

PROVIDER_INCEPTRON = "inceptron"

PROVIDER_INCO = "inco"

PROVIDER_INFER = "infer"
PROVIDER_INFERENCE = "inference"
PROVIDER_INFOMANIAK = "infomaniak"
PROVIDER_IO_NET = "io-net"
PROVIDER_ITERACOMPUTE = "iteracompute"
PROVIDER_JALAPENO = "jalapeno"
PROVIDER_JIEKOU = "jiekou"
PROVIDER_KLOKINTEGRATION = "klokintegration"
PROVIDER_KOSMIK = "kosmik"
PROVIDER_KUAE_CLOUD_CODING_PLAN = "kuae-cloud-coding-plan"
PROVIDER_LILAC = "lilac"
PROVIDER_LLAMA = "llama"
PROVIDER_LLMGATEWAY = "llmgateway"
PROVIDER_LLMGATEWAY_PROVIDERS = "llmgateway-providers"
PROVIDER_LLMTECH = "llmtech"
PROVIDER_LMSTUDIO = "lmstudio"
PROVIDER_LONGCAT = "longcat"
PROVIDER_LUCIDQUERY = "lucidquery"
PROVIDER_LYNKR = "lynkr"
PROVIDER_MEGANOVA = "meganova"
PROVIDER_MELIOUS = "melious"
PROVIDER_MERGE_GATEWAY = "merge-gateway"
PROVIDER_MINIMAX = "minimax"
PROVIDER_MINIMAX_CN = "minimax-cn"
PROVIDER_MINIMAX_CN_CODING_PLAN = "minimax-cn-coding-plan"
PROVIDER_MINIMAX_CODING_PLAN = "minimax-coding-plan"
PROVIDER_MISTRAL = "mistral"
PROVIDER_MIXLAYER = "mixlayer"
PROVIDER_MOARK = "moark"
PROVIDER_MODAL = "modal"
PROVIDER_MODEL_ORACLE_AI = "model-oracle-ai"
PROVIDER_MODELIS = "modelis"
PROVIDER_MODELSCOPE = "modelscope"
PROVIDER_MOONSHOTAI = "moonshotai"
PROVIDER_MOONSHOTAI_CN = "moonshotai-cn"
PROVIDER_MORPH = "morph"
PROVIDER_NAN = "nan"
PROVIDER_NEARAI = "nearai"
PROVIDER_NEBIUS = "nebius"
PROVIDER_NEON = "neon"
PROVIDER_NEOSMITH = "neosmith"
PROVIDER_NEURALWATT = "neuralwatt"
PROVIDER_NOVA = "nova"
PROVIDER_NOVITA_AI = "novita-ai"
PROVIDER_OCI = "oci"
PROVIDER_OFOX = "ofox"
PROVIDER_OLLAMA_CLOUD = "ollama-cloud"
PROVIDER_OPENAI = "openai"
PROVIDER_OPENCODE_GO = "opencode-go"
PROVIDER_OPENREASON = "openreason"
PROVIDER_OPPER = "opper"
PROVIDER_OVHCLOUD = "ovhcloud"
PROVIDER_PERPLEXITY = "perplexity"
PROVIDER_PERPLEXITY_AGENT = "perplexity-agent"
PROVIDER_PIONEER = "pioneer"
PROVIDER_POE = "poe"
PROVIDER_POOLSIDE = "poolside"
PROVIDER_PRIVATEMODE_AI = "privatemode-ai"
PROVIDER_QIHANG_AI = "qihang-ai"
PROVIDER_QINIU_AI = "qiniu-ai"
PROVIDER_QVAC = "qvac"
PROVIDER_REGOLO_AI = "regolo-ai"
PROVIDER_ROUTING_RUN = "routing-run"
PROVIDER_SAKANA = "sakana"
PROVIDER_SALAD_CLOUD = "salad-cloud"
PROVIDER_SAP_AI_CORE = "sap-ai-core"
PROVIDER_SARVAM = "sarvam"
PROVIDER_SCALEWAY = "scaleway"
PROVIDER_SCNET_TOKEN_PLAN = "scnet-token-plan"
PROVIDER_SCX_AI = "scx-ai"
PROVIDER_SENSENOVA = "sensenova"
PROVIDER_SILICONFLOW = "siliconflow"
PROVIDER_SILICONFLOW_CN = "siliconflow-cn"
PROVIDER_SNOWFLAKE_CORTEX = "snowflake-cortex"
PROVIDER_STACKIT = "stackit"
PROVIDER_STANDARDCOMPUTE = "standardcompute"
PROVIDER_STEPFUN = "stepfun"
PROVIDER_STEPFUN_AI = "stepfun-ai"
PROVIDER_STEPFUN_STEP_PLAN = "stepfun-step-plan"
PROVIDER_SUBCONSCIOUS = "subconscious"
PROVIDER_SUBMODEL = "submodel"
PROVIDER_SYNTHETIC = "synthetic"
PROVIDER_TEMPR = "tempr"
PROVIDER_TENCENT_CODING_PLAN = "tencent-coding-plan"
PROVIDER_TENCENT_TOKEN_PLAN = "tencent-token-plan"
PROVIDER_TENCENT_TOKENHUB = "tencent-tokenhub"
PROVIDER_TENSORX = "tensorx"
PROVIDER_THE_GRID_AI = "the-grid-ai"
PROVIDER_THINKINGMACHINES = "thinkingmachines"
PROVIDER_TINFOIL = "tinfoil"
PROVIDER_TOGETHERAI = "togetherai"
PROVIDER_TOKENGO = "tokengo"
PROVIDER_TRUSTEDROUTER = "trustedrouter"
PROVIDER_UMANS_AI = "umans-ai"
PROVIDER_UMANS_AI_CODING_PLAN = "umans-ai-coding-plan"
PROVIDER_UPSTAGE = "upstage"
PROVIDER_V0 = "v0"
PROVIDER_VANCINE = "vancine"
PROVIDER_VENICE = "venice"
PROVIDER_VISPARK = "vispark"
PROVIDER_VIVGRID = "vivgrid"
PROVIDER_VOLCENGINE = "volcengine"
PROVIDER_VOLCENGINE_CODING_PLAN = "volcengine-coding-plan"
PROVIDER_VULTR = "vultr"
PROVIDER_WAFER_AI = "wafer.ai"
PROVIDER_WALLABY = "wallaby"
PROVIDER_WANDB = "wandb"
PROVIDER_WATSONX = "watsonx"
PROVIDER_XAI = "xai"
PROVIDER_XIAOMI = "xiaomi"
PROVIDER_XIAOMI_TOKEN_PLAN_AMS = "xiaomi-token-plan-ams"
PROVIDER_XIAOMI_TOKEN_PLAN_CN = "xiaomi-token-plan-cn"
PROVIDER_XIAOMI_TOKEN_PLAN_SGP = "xiaomi-token-plan-sgp"
PROVIDER_XPERSONA = "xpersona"
PROVIDER_ZAI = "zai"
PROVIDER_ZAI_CODING_PLAN = "zai-coding-plan"
PROVIDER_ZELDOC = "zeldoc"
PROVIDER_ZENIFRA = "zenifra"
PROVIDER_ZHIPUAI = "zhipuai"
PROVIDER_ZHIPUAI_CODING_PLAN = "zhipuai-coding-plan"

SYNTHETIC_GET_GOAL_CALL_ID = "synthetic_get_goal"
SYNTHETIC_GET_GOAL_TOOL_NAME = "get_goal"

SENSITIVE_HEADER_NAMES: Set[str] = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
}

HOP_BY_HOP_HEADERS: Set[str] = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

CONFIG_DIR = Path.home() / ".config/mcptap"

_PROVIDER_ENV_FILES = {
    PROVIDER_OPENROUTER: "openrouter.env",
    PROVIDER_REQUESTY: "requesty.env",
    PROVIDER_META: "meta.env",
    PROVIDER_NANO_GPT: "nano-gpt.env",
    PROVIDER_LLMTR: "llmtr.env",
    PROVIDER_KENARI: "kenari.env",
    PROVIDER_UNOROUTER: "unorouter.env",
    PROVIDER_RUNINFRA: "runinfra.env",
    PROVIDER_NVIDIA: "nvidia.env",
    PROVIDER_OPENCODE: "opencode.env",
    PROVIDER_ORCAROUTER: "orcarouter.env",
    PROVIDER_AIHUBMIX: "aihubmix.env",
    PROVIDER_TOKENROUTER: "tokenrouter.env",
    PROVIDER_INFERX: "inferx.env",
    PROVIDER_KILO: "kilo.env",
    PROVIDER_PENDRA: "pendra.env",
    PROVIDER_VERCEL: "vercel.env",
    PROVIDER_ZENMUX: "zenmux.env",
    PROVIDER_ABLITERATION_AI: "abliteration-ai.env",
    PROVIDER_ABOVE: "above.env",
    PROVIDER_AGENTROUTER: "agentrouter.env",
    PROVIDER_AGNES: "agnes.env",
    PROVIDER_AI_ROUTER: "ai-router.env",
    PROVIDER_AIAND: "aiand.env",
    PROVIDER_AINETCAFE: "ainetcafe.env",
    PROVIDER_AIXY: "aixy.env",
    PROVIDER_AKI_IO: "aki-io.env",
    PROVIDER_ALIBABA: "alibaba.env",
    PROVIDER_302AI: "302ai.env",
    PROVIDER_ABACUS: "abacus.env",
    PROVIDER_ALIBABA_CN: "alibaba-cn.env",
    PROVIDER_ALIBABA_CODING_PLAN: "alibaba-coding-plan.env",
    PROVIDER_ALIBABA_CODING_PLAN_CN: "alibaba-coding-plan-cn.env",
    PROVIDER_ALIBABA_TOKEN_PLAN: "alibaba-token-plan.env",
    PROVIDER_ALIBABA_TOKEN_PLAN_CN: "alibaba-token-plan-cn.env",
    PROVIDER_AMAZON_BEDROCK: "amazon-bedrock.env",
    PROVIDER_AMBIENT: "ambient.env",
    PROVIDER_AMD: "amd.env",
    PROVIDER_ANTHROPIC: "anthropic.env",
    PROVIDER_ANYAPI: "anyapi.env",
    PROVIDER_ARCEE: "arcee.env",
    PROVIDER_ATOMIC_CHAT: "atomic-chat.env",
    PROVIDER_AURIKO: "auriko.env",
    PROVIDER_AZURE: "azure.env",
    PROVIDER_AZURE_COGNITIVE_SERVICES: "azure-cognitive-services.env",
    PROVIDER_BAILING: "bailing.env",
    PROVIDER_BASETEN: "baseten.env",
    PROVIDER_BERGET: "berget.env",
    PROVIDER_BLUECLAW: "blueclaw.env",
    PROVIDER_BOTHUB: "bothub.env",
    PROVIDER_CEREBRAS: "cerebras.env",
    PROVIDER_CHUTES: "chutes.env",
    PROVIDER_CLARIFAI: "clarifai.env",
    PROVIDER_CLAUDINIO: "claudinio.env",
    PROVIDER_CLINE_PASS: "cline-pass.env",
    PROVIDER_CLOUDFERRO_SHERLOCK: "cloudferro-sherlock.env",
    PROVIDER_CLOUDFLARE_AI_GATEWAY: "cloudflare-ai-gateway.env",
    PROVIDER_CLOUDFLARE_WORKERS_AI: "cloudflare-workers-ai.env",
    PROVIDER_COHERE: "cohere.env",
    PROVIDER_CORALBRICKS: "coralbricks.env",
    PROVIDER_CORTECS: "cortecs.env",
    PROVIDER_CROF: "crof.env",
    PROVIDER_CROSSMODEL: "crossmodel.env",
    PROVIDER_CRUSOE: "crusoe.env",
    PROVIDER_DAOXE: "daoxe.env",
    PROVIDER_DATABRICKS: "databricks.env",
    PROVIDER_DEEPINFRA: "deepinfra.env",
    PROVIDER_DEEPSEEK: "deepseek.env",
    PROVIDER_DIGITALOCEAN: "digitalocean.env",
    PROVIDER_DINFERENCE: "dinference.env",
    PROVIDER_DRUN: "drun.env",
    PROVIDER_EBCLOUD: "ebcloud.env",
    PROVIDER_ECHO: "echo.env",
    PROVIDER_EDENAI: "edenai.env",
    PROVIDER_EMPIRIOLABS: "empiriolabs.env",
    PROVIDER_EVROC: "evroc.env",
    PROVIDER_FASTROUTER: "fastrouter.env",
    PROVIDER_FIREWORKS_AI: "fireworks-ai.env",
    PROVIDER_FREEMODEL: "freemodel.env",
    PROVIDER_FRIENDLI: "friendli.env",
    PROVIDER_FROGBOT: "frogbot.env",
    PROVIDER_GITHUB_COPILOT: "github-copilot.env",
    PROVIDER_GMICLOUD: "gmicloud.env",
    PROVIDER_GOOGLE: "google.env",
    PROVIDER_GOOGLE_VERTEX: "google-vertex.env",
    PROVIDER_GOOGLE_VERTEX_ANTHROPIC: "google-vertex-anthropic.env",
    PROVIDER_GREENPT: "greenpt.env",
    PROVIDER_GROQ: "groq.env",
    PROVIDER_HELICONE: "helicone.env",
    PROVIDER_HETZNER: "hetzner.env",
    PROVIDER_HPC_AI: "hpc-ai.env",
    PROVIDER_HUGGINGFACE: "huggingface.env",
    PROVIDER_HYPER: "hyper.env",
    PROVIDER_IFLOWCN: "iflowcn.env",
    PROVIDER_IMPOSSIBL: "impossibl.env",
    PROVIDER_INCEPTION: "inception.env",
    PROVIDER_INCEPTRON: "inceptron.env",
    PROVIDER_INCO: "inco.env",
    PROVIDER_INFER: "infer.env",
    PROVIDER_INFERENCE: "inference.env",
    PROVIDER_INFOMANIAK: "infomaniak.env",
    PROVIDER_IO_NET: "io-net.env",
    PROVIDER_ITERACOMPUTE: "iteracompute.env",
    PROVIDER_JALAPENO: "jalapeno.env",
    PROVIDER_JIEKOU: "jiekou.env",
    PROVIDER_KLOKINTEGRATION: "klokintegration.env",
    PROVIDER_KOSMIK: "kosmik.env",
    PROVIDER_KUAE_CLOUD_CODING_PLAN: "kuae-cloud-coding-plan.env",
    PROVIDER_LILAC: "lilac.env",
    PROVIDER_LLAMA: "llama.env",
    PROVIDER_LLMGATEWAY: "llmgateway.env",
    PROVIDER_LLMGATEWAY_PROVIDERS: "llmgateway-providers.env",
    PROVIDER_LLMTECH: "llmtech.env",
    PROVIDER_LMSTUDIO: "lmstudio.env",
    PROVIDER_LONGCAT: "longcat.env",
    PROVIDER_LUCIDQUERY: "lucidquery.env",
    PROVIDER_LYNKR: "lynkr.env",
    PROVIDER_MEGANOVA: "meganova.env",
    PROVIDER_MELIOUS: "melious.env",
    PROVIDER_MERGE_GATEWAY: "merge-gateway.env",
    PROVIDER_MINIMAX: "minimax.env",
    PROVIDER_MINIMAX_CN: "minimax-cn.env",
    PROVIDER_MINIMAX_CN_CODING_PLAN: "minimax-cn-coding-plan.env",
    PROVIDER_MINIMAX_CODING_PLAN: "minimax-coding-plan.env",
    PROVIDER_MISTRAL: "mistral.env",
    PROVIDER_MIXLAYER: "mixlayer.env",
    PROVIDER_MOARK: "moark.env",
    PROVIDER_MODAL: "modal.env",
    PROVIDER_MODEL_ORACLE_AI: "model-oracle-ai.env",
    PROVIDER_MODELIS: "modelis.env",
    PROVIDER_MODELSCOPE: "modelscope.env",
    PROVIDER_MOONSHOTAI: "moonshotai.env",
    PROVIDER_MOONSHOTAI_CN: "moonshotai-cn.env",
    PROVIDER_MORPH: "morph.env",
    PROVIDER_NAN: "nan.env",
    PROVIDER_NEARAI: "nearai.env",
    PROVIDER_NEBIUS: "nebius.env",
    PROVIDER_NEON: "neon.env",
    PROVIDER_NEOSMITH: "neosmith.env",
    PROVIDER_NEURALWATT: "neuralwatt.env",
    PROVIDER_NOVA: "nova.env",
    PROVIDER_NOVITA_AI: "novita-ai.env",
    PROVIDER_OCI: "oci.env",
    PROVIDER_OFOX: "ofox.env",
    PROVIDER_OLLAMA_CLOUD: "ollama-cloud.env",
    PROVIDER_OPENAI: "openai.env",
    PROVIDER_OPENCODE_GO: "opencode-go.env",
    PROVIDER_OPENREASON: "openreason.env",
    PROVIDER_OPPER: "opper.env",
    PROVIDER_OVHCLOUD: "ovhcloud.env",
    PROVIDER_PERPLEXITY: "perplexity.env",
    PROVIDER_PERPLEXITY_AGENT: "perplexity-agent.env",
    PROVIDER_PIONEER: "pioneer.env",
    PROVIDER_POE: "poe.env",
    PROVIDER_POOLSIDE: "poolside.env",
    PROVIDER_PRIVATEMODE_AI: "privatemode-ai.env",
    PROVIDER_QIHANG_AI: "qihang-ai.env",
    PROVIDER_QINIU_AI: "qiniu-ai.env",
    PROVIDER_QVAC: "qvac.env",
    PROVIDER_REGOLO_AI: "regolo-ai.env",
    PROVIDER_ROUTING_RUN: "routing-run.env",
    PROVIDER_SAKANA: "sakana.env",
    PROVIDER_SALAD_CLOUD: "salad-cloud.env",
    PROVIDER_SAP_AI_CORE: "sap-ai-core.env",
    PROVIDER_SARVAM: "sarvam.env",
    PROVIDER_SCALEWAY: "scaleway.env",
    PROVIDER_SCNET_TOKEN_PLAN: "scnet-token-plan.env",
    PROVIDER_SCX_AI: "scx-ai.env",
    PROVIDER_SENSENOVA: "sensenova.env",
    PROVIDER_SILICONFLOW: "siliconflow.env",
    PROVIDER_SILICONFLOW_CN: "siliconflow-cn.env",
    PROVIDER_SNOWFLAKE_CORTEX: "snowflake-cortex.env",
    PROVIDER_STACKIT: "stackit.env",
    PROVIDER_STANDARDCOMPUTE: "standardcompute.env",
    PROVIDER_STEPFUN: "stepfun.env",
    PROVIDER_STEPFUN_AI: "stepfun-ai.env",
    PROVIDER_STEPFUN_STEP_PLAN: "stepfun-step-plan.env",
    PROVIDER_SUBCONSCIOUS: "subconscious.env",
    PROVIDER_SUBMODEL: "submodel.env",
    PROVIDER_SYNTHETIC: "synthetic.env",
    PROVIDER_TEMPR: "tempr.env",
    PROVIDER_TENCENT_CODING_PLAN: "tencent-coding-plan.env",
    PROVIDER_TENCENT_TOKEN_PLAN: "tencent-token-plan.env",
    PROVIDER_TENCENT_TOKENHUB: "tencent-tokenhub.env",
    PROVIDER_TENSORX: "tensorx.env",
    PROVIDER_THE_GRID_AI: "the-grid-ai.env",
    PROVIDER_THINKINGMACHINES: "thinkingmachines.env",
    PROVIDER_TINFOIL: "tinfoil.env",
    PROVIDER_TOGETHERAI: "togetherai.env",
    PROVIDER_TOKENGO: "tokengo.env",
    PROVIDER_TRUSTEDROUTER: "trustedrouter.env",
    PROVIDER_UMANS_AI: "umans-ai.env",
    PROVIDER_UMANS_AI_CODING_PLAN: "umans-ai-coding-plan.env",
    PROVIDER_UPSTAGE: "upstage.env",
    PROVIDER_V0: "v0.env",
    PROVIDER_VANCINE: "vancine.env",
    PROVIDER_VENICE: "venice.env",
    PROVIDER_VISPARK: "vispark.env",
    PROVIDER_VIVGRID: "vivgrid.env",
    PROVIDER_VOLCENGINE: "volcengine.env",
    PROVIDER_VOLCENGINE_CODING_PLAN: "volcengine-coding-plan.env",
    PROVIDER_VULTR: "vultr.env",
    PROVIDER_WAFER_AI: "wafer.ai.env",
    PROVIDER_WALLABY: "wallaby.env",
    PROVIDER_WANDB: "wandb.env",
    PROVIDER_WATSONX: "watsonx.env",
    PROVIDER_XAI: "xai.env",
    PROVIDER_XIAOMI: "xiaomi.env",
    PROVIDER_XIAOMI_TOKEN_PLAN_AMS: "xiaomi-token-plan-ams.env",
    PROVIDER_XIAOMI_TOKEN_PLAN_CN: "xiaomi-token-plan-cn.env",
    PROVIDER_XIAOMI_TOKEN_PLAN_SGP: "xiaomi-token-plan-sgp.env",
    PROVIDER_XPERSONA: "xpersona.env",
    PROVIDER_ZAI: "zai.env",
    PROVIDER_ZAI_CODING_PLAN: "zai-coding-plan.env",
    PROVIDER_ZELDOC: "zeldoc.env",
    PROVIDER_ZENIFRA: "zenifra.env",
    PROVIDER_ZHIPUAI: "zhipuai.env",
    PROVIDER_ZHIPUAI_CODING_PLAN: "zhipuai-coding-plan.env",
}

_UPSTREAM_BASE_URLS = {
    PROVIDER_OPENROUTER: "https://openrouter.ai/api/v1",
    PROVIDER_REQUESTY: "https://router.requesty.ai/v1",
    PROVIDER_META: "https://api.meta.ai/v1",
    PROVIDER_NANO_GPT: "https://nano-gpt.com/api/v1",
    PROVIDER_LLMTR: "https://llmtr.com/v1",
    PROVIDER_KENARI: "https://kenari.id/v1",
    PROVIDER_UNOROUTER: "https://api.unorouter.com/v1",
    PROVIDER_RUNINFRA: "https://api.runinfra.ai/v1",
    PROVIDER_NVIDIA: "https://integrate.api.nvidia.com/v1",
    PROVIDER_OPENCODE: "https://opencode.ai/zen/v1",
    PROVIDER_ORCAROUTER: "https://api.orcarouter.ai/v1",
    PROVIDER_AIHUBMIX: "https://aihubmix.com/v1",
    PROVIDER_TOKENROUTER: "https://api.tokenrouter.com/v1",
    PROVIDER_INFERX: "https://model.inferx.net/endpoints/v1",
    PROVIDER_KILO: "https://api.kilo.ai/api/gateway",
    PROVIDER_PENDRA: "https://api.pendra.ai/api/v1",
    PROVIDER_VERCEL: "https://ai-gateway.vercel.sh/v1",
    PROVIDER_ZENMUX: "https://zenmux.ai/api/v1",
    PROVIDER_ABLITERATION_AI: "https://api.abliteration.ai/v1",
    PROVIDER_ABOVE: "https://api.above.dev/v1",
    PROVIDER_AGENTROUTER: "https://agentrouter.org/v1",
    PROVIDER_AGNES: "https://apihub.agnes-ai.com/v1",
    PROVIDER_AI_ROUTER: "https://api.ai-router.dev/v1",
    PROVIDER_AIAND: "https://api.aiand.com/v1",
    PROVIDER_AINETCAFE: "https://microquickjs.com/v1",
    PROVIDER_AIXY: "https://api.aixy-gateway.com/v1",
    PROVIDER_AKI_IO: "https://aki.io/v1",
    PROVIDER_ALIBABA: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    PROVIDER_302AI: "https://api.302.ai/v1",
    PROVIDER_ABACUS: "https://routellm.abacus.ai/v1",
    PROVIDER_ALIBABA_CN: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    PROVIDER_ALIBABA_CODING_PLAN: "https://coding-intl.dashscope.aliyuncs.com/v1",
    PROVIDER_ALIBABA_CODING_PLAN_CN: "https://coding.dashscope.aliyuncs.com/v1",
    PROVIDER_ALIBABA_TOKEN_PLAN: "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    PROVIDER_ALIBABA_TOKEN_PLAN_CN: "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    PROVIDER_AMAZON_BEDROCK: "https://bedrock-runtime.us-east-1.amazonaws.com",
    PROVIDER_AMBIENT: "https://api.ambient.xyz/v1",
    PROVIDER_AMD: "https://developer.amd.com.cn/radeon/api/v1",
    PROVIDER_ANTHROPIC: "https://api.anthropic.com/v1",
    PROVIDER_ANYAPI: "https://api.anyapi.ai/v1",
    PROVIDER_ARCEE: "https://api.arcee.ai/api/v1",
    PROVIDER_ATOMIC_CHAT: "http://127.0.0.1:1337/v1",
    PROVIDER_AURIKO: "https://api.auriko.ai/v1",
    PROVIDER_AZURE: "https://YOUR_RESOURCE.openai.azure.com/openai/v1",
    PROVIDER_AZURE_COGNITIVE_SERVICES: "https://YOUR_RESOURCE.services.ai.azure.com/openai/v1",
    PROVIDER_BAILING: "https://api.tbox.cn/api/llm/v1",
    PROVIDER_BASETEN: "https://inference.baseten.co/v1",
    PROVIDER_BERGET: "https://api.berget.ai/v1",
    PROVIDER_BLUECLAW: "https://openai.blueclaw.network/v1",
    PROVIDER_BOTHUB: "https://openai.bothub.ru/v1",
    PROVIDER_CEREBRAS: "https://api.cerebras.ai/v1",
    PROVIDER_CHUTES: "https://llm.chutes.ai/v1",
    PROVIDER_CLARIFAI: "https://api.clarifai.com/v2/ext/openai/v1",
    PROVIDER_CLAUDINIO: "https://api.claudin.io/v1",
    PROVIDER_CLINE_PASS: "https://api.cline.bot/api/v1",
    PROVIDER_CLOUDFERRO_SHERLOCK: "https://api-sherlock.cloudferro.com/openai/v1",
    PROVIDER_CLOUDFLARE_AI_GATEWAY: "https://gateway.ai.cloudflare.com/v1",
    PROVIDER_COHERE: "https://api.cohere.ai/compatibility/v1",
    PROVIDER_CORALBRICKS: "https://inference.coralbricks.ai/v1",
    PROVIDER_CORTECS: "https://api.cortecs.ai/v1",
    PROVIDER_CROF: "https://crof.ai/v1",
    PROVIDER_CROSSMODEL: "https://api.crossmodel.ai/v1",
    PROVIDER_CRUSOE: "https://api.inference.crusoecloud.com/v1",
    PROVIDER_DAOXE: "https://daoxe.com/v1",
    PROVIDER_DEEPINFRA: "https://api.deepinfra.com/v1/openai",
    PROVIDER_DEEPSEEK: "https://api.deepseek.com",
    PROVIDER_DIGITALOCEAN: "https://inference.do-ai.run/v1",
    PROVIDER_DINFERENCE: "https://api.dinference.com/v1",
    PROVIDER_DRUN: "https://chat.d.run/v1",
    PROVIDER_EBCLOUD: "https://maas-api.ebcloud.com/v1",
    PROVIDER_ECHO: "https://echo.tracerml.ai/v1",
    PROVIDER_EDENAI: "https://api.edenai.run/v3",
    PROVIDER_EMPIRIOLABS: "https://api.empiriolabs.ai/v1",
    PROVIDER_EVROC: "https://models.think.evroc.com/v1",
    PROVIDER_FASTROUTER: "https://go.fastrouter.ai/api/v1",
    PROVIDER_FIREWORKS_AI: "https://api.fireworks.ai/inference/v1",
    PROVIDER_FREEMODEL: "https://cc.freemodel.dev/v1",
    PROVIDER_FRIENDLI: "https://api.friendli.ai/serverless/v1",
    PROVIDER_FROGBOT: "https://app.frogbot.ai/api/v1",
    PROVIDER_GITHUB_COPILOT: "https://api.githubcopilot.com",
    PROVIDER_GMICLOUD: "https://api.gmi-serving.com/v1",
    PROVIDER_GOOGLE: "https://generativelanguage.googleapis.com/v1beta/openai",
    PROVIDER_GOOGLE_VERTEX: "https://us-central1-aiplatform.googleapis.com/v1",
    PROVIDER_GOOGLE_VERTEX_ANTHROPIC: "https://us-central1-aiplatform.googleapis.com/v1",
    PROVIDER_GREENPT: "https://api.greenpt.ai/v1",
    PROVIDER_GROQ: "https://api.groq.com/openai/v1",
    PROVIDER_HELICONE: "https://ai-gateway.helicone.ai/v1",
    PROVIDER_HETZNER: "https://inference.hetzner.com/api/v1",
    PROVIDER_HPC_AI: "https://api.hpc-ai.com/inference/v1",
    PROVIDER_HUGGINGFACE: "https://router.huggingface.co/v1",
    PROVIDER_HYPER: "https://hyper.charm.land/v1",
    PROVIDER_IFLOWCN: "https://apis.iflow.cn/v1",
    PROVIDER_IMPOSSIBL: "https://api.impossibl.com/v1",
    PROVIDER_INCEPTION: "https://api.inceptionlabs.ai/v1",
    PROVIDER_INCEPTRON: "https://api.inceptron.io/v1",
    PROVIDER_INCO: "https://api.inco.ai/v1",
    PROVIDER_INFER: "https://infer.flow7.org/v1",
    PROVIDER_INFERENCE: "https://inference.net/v1",
    PROVIDER_INFOMANIAK: "https://api.infomaniak.com/2/ai/${INFOMANIAK_PRODUCT_ID}/openai/v1",
    PROVIDER_IO_NET: "https://api.intelligence.io.solutions/api/v1",
    PROVIDER_ITERACOMPUTE: "https://api.iteracompute.com/v1",
    PROVIDER_JALAPENO: "https://api.jalapeno-cloud.ai/v1",
    PROVIDER_JIEKOU: "https://api.jiekou.ai/openai",
    PROVIDER_KLOKINTEGRATION: "https://api-gw.klok.ipaas.se/proxy/kloker-key/v1",
    PROVIDER_KOSMIK: "https://api.koscompute.com/v1",
    PROVIDER_KUAE_CLOUD_CODING_PLAN: "https://coding-plan-endpoint.kuaecloud.net/v1",
    PROVIDER_LILAC: "https://api.getlilac.com/v1",
    PROVIDER_LLAMA: "https://api.llama.com/compat/v1",
    PROVIDER_LLMGATEWAY: "https://api.llmgateway.io/v1",
    PROVIDER_LLMGATEWAY_PROVIDERS: "https://api.llmgateway.io/v1",
    PROVIDER_LLMTECH: "https://api.llmtech.eu/v1",
    PROVIDER_LMSTUDIO: "http://127.0.0.1:1234/v1",
    PROVIDER_LONGCAT: "https://api.longcat.chat/openai",
    PROVIDER_LUCIDQUERY: "https://api.lucidquery.com/v1",
    PROVIDER_LYNKR: "http://127.0.0.1:8081/v1",
    PROVIDER_MEGANOVA: "https://api.meganova.ai/v1",
    PROVIDER_MELIOUS: "https://api.melious.ai/v1",
    PROVIDER_MERGE_GATEWAY: "https://api-gateway.merge.dev/v1/ai-sdk",
    PROVIDER_MINIMAX: "https://api.minimax.io/anthropic/v1",
    PROVIDER_MINIMAX_CN: "https://api.minimax.cn/anthropic/v1",
    PROVIDER_MINIMAX_CN_CODING_PLAN: "https://api.minimax.cn/anthropic/v1",
    PROVIDER_MINIMAX_CODING_PLAN: "https://api.minimax.io/anthropic/v1",
    PROVIDER_MISTRAL: "https://api.mistral.ai/v1",
    PROVIDER_MIXLAYER: "https://models.mixlayer.ai/v1",
    PROVIDER_MOARK: "https://moark.com/v1",
    PROVIDER_MODAL: "https://inference.us-west.modal.direct/v1",
    PROVIDER_MODEL_ORACLE_AI: "https://api.modeloracle.com/api/v1",
    PROVIDER_MODELIS: "https://modelishub.com/v1",
    PROVIDER_MODELSCOPE: "https://api-inference.modelscope.cn/v1",
    PROVIDER_MOONSHOTAI: "https://api.moonshot.ai/v1",
    PROVIDER_MOONSHOTAI_CN: "https://api.moonshot.cn/v1",
    PROVIDER_MORPH: "https://api.morphllm.com/v1",
    PROVIDER_NAN: "https://api.nan.builders/v1",
    PROVIDER_NEARAI: "https://cloud-api.near.ai/v1",
    PROVIDER_NEBIUS: "https://api.tokenfactory.nebius.com/v1",
    PROVIDER_NEON: "${NEON_AI_GATEWAY_BASE_URL}/v1",
    PROVIDER_NEOSMITH: "https://router.neosmith.ai/v1",
    PROVIDER_NEURALWATT: "https://api.neuralwatt.com/v1",
    PROVIDER_NOVA: "https://api.nova.amazon.com/v1",
    PROVIDER_NOVITA_AI: "https://api.novita.ai/openai",
    PROVIDER_OCI: "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1",
    PROVIDER_OFOX: "https://api.ofox.ai/v1",
    PROVIDER_OLLAMA_CLOUD: "https://ollama.com/v1",
    PROVIDER_OPENAI: "https://api.openai.com/v1",
    PROVIDER_OPENCODE_GO: "https://opencode.ai/zen/go/v1",
    PROVIDER_OPENREASON: "https://api.openreason.app/v1",
    PROVIDER_OPPER: "https://api.opper.ai/v3/compat",
    PROVIDER_OVHCLOUD: "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
    PROVIDER_PERPLEXITY: "https://api.perplexity.ai",
    PROVIDER_PERPLEXITY_AGENT: "https://api.perplexity.ai/v1",
    PROVIDER_PIONEER: "https://api.pioneer.ai/v1",
    PROVIDER_POE: "https://api.poe.com/v1",
    PROVIDER_POOLSIDE: "https://inference.poolside.ai/v1",
    PROVIDER_PRIVATEMODE_AI: "http://localhost:8080/v1",
    PROVIDER_QIHANG_AI: "https://api.qhaigc.net/v1",
    PROVIDER_QINIU_AI: "https://api.qnaigc.com/v1",
    PROVIDER_QVAC: "http://127.0.0.1:11434/v1",
    PROVIDER_REGOLO_AI: "https://api.regolo.ai/v1",
    PROVIDER_ROUTING_RUN: "https://api.routing.run/v1",
    PROVIDER_SAKANA: "https://api.sakana.ai/v1",
    PROVIDER_SALAD_CLOUD: "https://ai.salad.cloud/v1",
    PROVIDER_SAP_AI_CORE: "https://api.ai.prod.eu-central-1.aws.ml.hana.ondemand.com",
    PROVIDER_SARVAM: "https://api.sarvam.ai/v1",
    PROVIDER_SCALEWAY: "https://api.scaleway.ai/v1",
    PROVIDER_SCNET_TOKEN_PLAN: "https://api.scnet.cn/api/llm/v1",
    PROVIDER_SCX_AI: "https://api.scx.ai/v1",
    PROVIDER_SENSENOVA: "https://token.sensenova.cn/v1",
    PROVIDER_SILICONFLOW: "https://api.siliconflow.com/v1",
    PROVIDER_SILICONFLOW_CN: "https://api.siliconflow.cn/v1",
    PROVIDER_SNOWFLAKE_CORTEX: "https://${SNOWFLAKE_ACCOUNT}.snowflakecomputing.com/api/v2/cortex/v1",
    PROVIDER_STACKIT: "https://api.openai-compat.model-serving.eu01.onstackit.cloud/v1",
    PROVIDER_STANDARDCOMPUTE: "https://api.stdcmpt.com/v1",
    PROVIDER_STEPFUN: "https://api.stepfun.com/v1",
    PROVIDER_STEPFUN_AI: "https://api.stepfun.ai/v1",
    PROVIDER_STEPFUN_STEP_PLAN: "https://api.stepfun.com/step_plan/v1",
    PROVIDER_SUBCONSCIOUS: "https://api.subconscious.dev/v1",
    PROVIDER_SUBMODEL: "https://llm.submodel.ai/v1",
    PROVIDER_SYNTHETIC: "https://api.synthetic.new/openai/v1",
    PROVIDER_TEMPR: "https://api.temprhq.io/v1",
    PROVIDER_TENCENT_CODING_PLAN: "https://api.lkeap.cloud.tencent.com/coding/v3",
    PROVIDER_TENCENT_TOKEN_PLAN: "https://api.lkeap.cloud.tencent.com/plan/v3",
    PROVIDER_TENCENT_TOKENHUB: "https://tokenhub.tencentmaas.com/v1",
    PROVIDER_TENSORX: "https://api.tensorx.ai/v1",
    PROVIDER_THE_GRID_AI: "https://api.thegrid.ai/v1",
    PROVIDER_THINKINGMACHINES: "https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api/v1",
    PROVIDER_TINFOIL: "https://inference.tinfoil.sh/v1",
    PROVIDER_TOGETHERAI: "https://api.together.ai/v1",
    PROVIDER_TOKENGO: "https://api.tokengo.com/v1",
    PROVIDER_TRUSTEDROUTER: "https://api.trustedrouter.com/v1",
    PROVIDER_UMANS_AI: "https://api.code.umans.ai/v1",
    PROVIDER_UMANS_AI_CODING_PLAN: "https://api.code.umans.ai/v1",
    PROVIDER_UPSTAGE: "https://api.upstage.ai/v1/solar",
    PROVIDER_V0: "https://api.v0.dev/v1",
    PROVIDER_VANCINE: "https://vancine.com/v1",
    PROVIDER_VENICE: "https://api.venice.ai/api/v1",
    PROVIDER_VISPARK: "https://api.lab.vispark.in/v1",
    PROVIDER_VIVGRID: "https://api.vivgrid.com/v1",
    PROVIDER_VOLCENGINE: "https://ark.cn-beijing.volces.com/api/v3",
    PROVIDER_VOLCENGINE_CODING_PLAN: "https://ark.cn-beijing.volces.com/api/coding/v3",
    PROVIDER_VULTR: "https://api.vultrinference.com/v1",
    PROVIDER_WAFER_AI: "https://pass.wafer.ai/v1",
    PROVIDER_WALLABY: "https://api.wallabytoken.com/v1",
    PROVIDER_WANDB: "https://api.inference.wandb.ai/v1",
    PROVIDER_WATSONX: "https://us-south.ml.cloud.ibm.com",
    PROVIDER_XAI: "https://api.x.ai/v1",
    PROVIDER_XIAOMI: "https://api.xiaomimimo.com/v1",
    PROVIDER_XIAOMI_TOKEN_PLAN_AMS: "https://token-plan-ams.xiaomimimo.com/v1",
    PROVIDER_XIAOMI_TOKEN_PLAN_CN: "https://token-plan-cn.xiaomimimo.com/v1",
    PROVIDER_XIAOMI_TOKEN_PLAN_SGP: "https://token-plan-sgp.xiaomimimo.com/v1",
    PROVIDER_XPERSONA: "https://www.xpersona.co/v1",
    PROVIDER_ZAI: "https://api.z.ai/api/paas/v4",
    PROVIDER_ZAI_CODING_PLAN: "https://api.z.ai/api/coding/paas/v4",
    PROVIDER_ZELDOC: "https://api.zeldoc.ai/v1",
    PROVIDER_ZENIFRA: "https://ai.zenifra.com/v1",
    PROVIDER_ZHIPUAI: "https://open.bigmodel.cn/api/paas/v4",
    PROVIDER_ZHIPUAI_CODING_PLAN: "https://open.bigmodel.cn/api/coding/paas/v4",
}


def get_provider_api_key(provider: str) -> str:
    """Read an API key directly from the selected provider configuration file."""
    provider_name = provider.strip().lower()
    provider_env_file = _PROVIDER_ENV_FILES.get(provider_name)
    if provider_env_file is None:
        raise ValueError(f"Unsupported provider: {provider}")

    api_key = (dotenv_values(CONFIG_DIR / provider_env_file).get("MCPTAP_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(f"MCPTAP_API_KEY must not be empty in {provider_env_file}")
    return api_key


# Keys that ``load_dotenv`` injects from provider env files. These must be
# cleaned from ``os.environ`` before loading a different provider file to avoid
# stale values leaking across provider switches.
_PROVIDER_ENV_KEYS = [
    "MCPTAP_API_KEY",
    "MCPTAP_MODEL",
    "MCPTAP_PLAN_MODE_MODEL",
    "MCPTAP_OPENROUTER_PROVIDER",
    "MCPTAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS",
    "MCPTAP_CREDITS_URL",
    "MCPTAP_CREDITS_API_KEY",
    "MCPTAP_USE_CHAT_COMPLETIONS",
]


@dataclass
class Settings:
    """Immutable configuration loaded from environment and .env files."""

    # Network
    listen_host: str
    listen_port: int

    # Upstream provider
    upstream_provider: str
    upstream_base_url: str
    provider_env_file: str
    api_key: str
    use_chat_completions: bool

    # Model forcing
    model: str
    plan_mode_model: str
    plan_mode_trigger: str
    plan_mode_max_input_size: int

    # OpenRouter provider pinning
    openrouter_provider: str
    openrouter_disable_provider_fallbacks: bool

    # Credits API
    credits_url: str
    credits_api_key: str
    credits_check_interval: int
    credits_discrepancy_threshold: float

    # Telegram alerts
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_alert_level: str

    # MCP intercept
    intercept_yaml: str
    intercept_max_iterations: int
    intercept_tool_timeout: float

    # Per-model instructions
    per_model_yaml: str

    # Logging
    log_level: str
    log_file: str
    log_file_redact_headers: bool
    log_payload_keys: List[str]

    # Tool-call hook
    use_tool_hook: str
    use_tool_hook_timeout: float
    use_tool_hook_synthetic_tool: str
    use_tool_hook_pending_ttl: float

    # Per-session blocklist directory
    per_session_dir: str

    # Request log database
    log_db_path: str
    log_retention_days: int

    # Pareto data provider
    pareto_provider: str = PROVIDER_OPENROUTER


class _SettingsProxy:
    """Transparent proxy that delegates attribute access to the current Settings.

    ``from mcptap.settings import settings`` binds to this proxy object.
    When ``reload_settings()`` swaps the internal instance, all existing
    references immediately see the new values without re-importing.
    """

    __slots__ = ("_target",)

    def __init__(self, target: Settings) -> None:
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name: str):
        return getattr(self._target, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_target":
            object.__setattr__(self, name, value)
        else:
            setattr(self._target, name, value)

    def __repr__(self) -> str:
        return f"_SettingsProxy({self._target!r})"

    def _swap(self, target: Settings) -> None:
        """Replace the proxied Settings instance (internal use)."""
        object.__setattr__(self, "_target", target)


def _load_env_files() -> None:
    """Load proxy.env and the provider-specific env file into os.environ.

    Stale provider keys are removed before loading a new provider file to
    prevent values from one provider leaking into another.
    """
    load_dotenv(CONFIG_DIR / "proxy.env", override=True)

    upstream_provider = (os.environ.get("MCPTAP_UPSTREAM_PROVIDER") or "").strip().lower()

    provider_env_file = _PROVIDER_ENV_FILES.get(upstream_provider, "")
    if not provider_env_file:
        raise RuntimeError(
            "MCPTAP_UPSTREAM_PROVIDER must be one of 'openrouter', 'requesty', 'meta', 'nano-gpt', 'llmtr'"
        )

    # Remove stale provider-specific keys before loading the new provider file.
    for key in _PROVIDER_ENV_KEYS:
        os.environ.pop(key, None)

    load_dotenv(CONFIG_DIR / provider_env_file, override=True)


def _build_settings() -> Settings:
    """Build a Settings instance from the current os.environ."""
    upstream_provider = (os.environ.get("MCPTAP_UPSTREAM_PROVIDER") or "").strip().lower()

    upstream_base_url = _UPSTREAM_BASE_URLS.get(upstream_provider, "")
    provider_env_file = _PROVIDER_ENV_FILES.get(upstream_provider, "")
    if not upstream_base_url:
        raise RuntimeError(
            "MCPTAP_UPSTREAM_PROVIDER must be one of 'openrouter', 'requesty', 'meta', 'nano-gpt', 'llmtr'"
        )

    api_key = (os.environ.get("MCPTAP_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("MCPTAP_API_KEY must not be empty")

    model = (os.environ.get("MCPTAP_MODEL") or "").strip()
    plan_mode_model = (os.environ.get("MCPTAP_PLAN_MODE_MODEL") or "").strip()
    if not model or not plan_mode_model:
        raise RuntimeError("MCPTAP_MODEL and MCPTAP_PLAN_MODE_MODEL must not be empty")

    # Requesty model name normalization
    if PROVIDER_REQUESTY == upstream_provider:
        if model.startswith("openai") and "-responses" not in model:
            vendor, mdl = model.split("/", 1)
            model = f"{vendor}-responses/{mdl}"
        if plan_mode_model.startswith("openai") and "-responses" not in plan_mode_model:
            vendor, mdl = plan_mode_model.split("/", 1)
            plan_mode_model = f"{vendor}-responses/{mdl}"
        model = model.split(":")[0]
        plan_mode_model = plan_mode_model.split(":")[0]

    openrouter_provider = (os.environ.get("MCPTAP_OPENROUTER_PROVIDER") or "").strip()
    openrouter_disable_provider_fallbacks = os.environ.get(
        "MCPTAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS", "1"
    ).lower() not in {"0", "false", "no", "off"}

    credits_url = (os.environ.get("MCPTAP_CREDITS_URL") or "").strip()
    credits_api_key = (os.environ.get("MCPTAP_CREDITS_API_KEY") or "").strip()
    credits_check_interval = int(os.environ.get("MCPTAP_CREDITS_CHECK_INTERVAL", "300"))
    credits_discrepancy_threshold = float(os.environ.get("MCPTAP_CREDITS_DISCREPANCY_THRESHOLD", "0.01"))
    use_chat_completions = os.environ.get("MCPTAP_USE_CHAT_COMPLETIONS", "0").lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }

    telegram_bot_token = (os.environ.get("MCPTAP_TELEGRAM_BOT_TOKEN") or "").strip()
    telegram_chat_id = (os.environ.get("MCPTAP_TELEGRAM_CHAT_ID") or "").strip()
    telegram_alert_level = (os.environ.get("MCPTAP_TELEGRAM_ALERT_LEVEL") or "mismatch").strip().lower()

    _synthetic_tool_env = os.environ.get("MCPTAP_USE_TOOL_HOOK_SYNTHETIC_TOOL")
    use_tool_hook_synthetic_tool = _synthetic_tool_env.strip() if _synthetic_tool_env is not None else "get_goal"

    return Settings(
        listen_host=os.environ.get("MCPTAP_LISTEN_HOST", "127.0.0.1"),
        listen_port=int(os.environ.get("MCPTAP_LISTEN_PORT", "8787")),
        upstream_provider=upstream_provider,
        upstream_base_url=upstream_base_url,
        provider_env_file=provider_env_file,
        api_key=api_key,
        use_chat_completions=use_chat_completions,
        model=model,
        plan_mode_model=plan_mode_model,
        plan_mode_trigger=(os.environ.get("MCPTAP_PLAN_MODE_TRIGGER") or "max").strip(),
        plan_mode_max_input_size=int(os.environ.get("MCPTAP_PLAN_MODE_MAX_INPUT_SIZE", 100000)),
        openrouter_provider=openrouter_provider,
        openrouter_disable_provider_fallbacks=openrouter_disable_provider_fallbacks,
        credits_url=credits_url,
        credits_api_key=credits_api_key,
        credits_check_interval=credits_check_interval,
        credits_discrepancy_threshold=credits_discrepancy_threshold,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        telegram_alert_level=telegram_alert_level,
        intercept_yaml=(os.environ.get("MCPTAP_INTERCEPT_YAML") or "").strip(),
        intercept_max_iterations=int(os.environ.get("MCPTAP_INTERCEPT_MAX_ITERATIONS", "8")),
        intercept_tool_timeout=float(os.environ.get("MCPTAP_INTERCEPT_TOOL_TIMEOUT", "120")),
        per_model_yaml=(os.environ.get("MCPTAP_PER_MODEL_YAML") or "").strip(),
        log_level=(os.environ.get("MCPTAP_LOG_LEVEL") or "INFO").upper(),
        log_file=(os.environ.get("MCPTAP_LOG_FILE") or "").strip(),
        log_file_redact_headers=(
            os.environ.get("LOG_FILE_REDACT_HEADERS", "0").lower() not in {"0", "false", "no", "off"}
        ),
        log_payload_keys=["tools"],
        use_tool_hook=(os.environ.get("MCPTAP_USE_TOOL_HOOK") or "").strip(),
        use_tool_hook_timeout=float(os.environ.get("MCPTAP_USE_TOOL_HOOK_TIMEOUT", "30")),
        use_tool_hook_synthetic_tool=use_tool_hook_synthetic_tool,
        use_tool_hook_pending_ttl=float(os.environ.get("MCPTAP_USE_TOOL_HOOK_PENDING_TTL", "600")),
        per_session_dir=(os.environ.get("MCPTAP_PER_SESSION_DIR") or "/tmp/mcptap/per_session").strip(),
        log_db_path=(os.environ.get("MCPTAP_LOG_DB") or os.path.expanduser("~/.local/share/mcptap/logs.db")).strip(),
        log_retention_days=int(os.environ.get("MCPTAP_LOG_RETENTION_DAYS", "7")),
        pareto_provider=(os.environ.get("MCPTAP_PARETO_PROVIDER") or PROVIDER_OPENROUTER).strip().lower(),
    )


def _setup_logging(s: Settings) -> logging.Logger:
    """Configure root and communication loggers based on settings."""
    logging.basicConfig(
        level=getattr(logging, s.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("mcptap")
    logger.setLevel(getattr(logging, s.log_level, logging.INFO))

    comm_logger = logging.getLogger("mcptap-communication")
    comm_logger.propagate = False
    comm_logger.setLevel(logging.INFO)
    if s.log_file:
        handler = logging.FileHandler(s.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        comm_logger.addHandler(handler)

    return logger


def reload_settings() -> Settings:
    """Reload env files, rebuild Settings, swap the proxy target, reconfigure logging.

    Returns the new Settings instance.
    """
    _load_env_files()
    new_settings = _build_settings()
    settings._swap(new_settings)
    _setup_logging(new_settings)
    LOGGER.info("Settings reloaded: provider=%s model=%s", new_settings.upstream_provider, new_settings.model)
    return new_settings


# ---------------------------------------------------------------------------
# Module-level initialization
# ---------------------------------------------------------------------------

# Load env files into os.environ on first import.
_load_env_files()

# Proxy created once at import time. reload_settings() swaps the internal
# target so all callers see the new values.
settings: Settings = _SettingsProxy(_build_settings())  # type: ignore
LOGGER = _setup_logging(settings)
COMMUNICATION_LOGGER = logging.getLogger("mcptap-communication")

# Debug payload keys for logging
DEBUG_PAYLOAD_KEYS: List[str] = ["tools"]
