"""Hot-reload of configuration files at runtime.

Polls mtime of nine configuration files and triggers a selective reload
cascade when any of them changes:

  proxy.env         -> reload env files + Settings + all dependent components
  openrouter.env    -> reload env files + Settings + all dependent components
  requesty.env      -> reload env files + Settings + all dependent components
  meta.env          -> reload env files + Settings + all dependent components
  nano-gpt.env      -> reload env files + Settings + all dependent components
  llmtr.env         -> reload env files + Settings + all dependent components
  [...].env         -> reload env files + Settings + all dependent components
  mcp-intercept.yaml -> reload MCPInterceptor (stop old subprocess, start new)
  per-model.yaml    -> reload per-model config dict
  use_tool_hook.py   -> reload tool hook enabled flag + Settings (path may change)

The reloader runs as a background asyncio task inside the aiohttp event loop.
It is designed for a development tool: simplicity over perfection.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from mcptap.mcp_intercept import MCPInterceptor, load_intercept_config
from mcptap.rewrite import load_per_model_config
from mcptap.settings import CONFIG_DIR, LOGGER, reload_settings, settings

# Poll interval (seconds).
_POLL_INTERVAL = 2.0

# Config file basenames relative to CONFIG_DIR.
_FILE_PROXY_ENV = "proxy.env"
_FILE_OPENROUTER_ENV = "openrouter.env"
_FILE_REQUESTY_ENV = "requesty.env"
_FILE_META_ENV = "meta.env"
_FILE_NANO_GPT_ENV = "nano-gpt.env"
_FILE_LLMTR_ENV = "llmtr.env"
_FILE_KENARI_ENV = "kenari.env"
_FILE_UNOROUTER_ENV = "unorouter.env"
_FILE_RUNINFRA_ENV = "runinfra.env"
_FILE_NVIDIA_ENV = "nvidia.env"
_FILE_OPENCODE_ENV = "opencode.env"
_FILE_ORCAROUTER_ENV = "orcarouter.env"
_FILE_AIHUBMIX_ENV = "aihubmix.env"
_FILE_TOKENROUTER_ENV = "tokenrouter.env"
_FILE_INFERX_ENV = "inferx.env"
_FILE_KILO_ENV = "kilo.env"
_FILE_PENDRA_ENV = "pendra.env"
_FILE_VERCEL_ENV = "vercel.env"
_FILE_ZENMUX_ENV = "zenmux.env"
_FILE_ABLITERATION_AI_ENV = "abliteration-ai.env"
_FILE_ABOVE_ENV = "above.env"
_FILE_AGENTROUTER_ENV = "agentrouter.env"
_FILE_AGNES_ENV = "agnes.env"
_FILE_AI_ROUTER_ENV = "ai-router.env"
_FILE_AIAND_ENV = "aiand.env"
_FILE_AINETCAFE_ENV = "ainetcafe.env"
_FILE_AIXY_ENV = "aixy.env"
_FILE_AKI_IO_ENV = "aki-io.env"
_FILE_ALIBABA_ENV = "alibaba.env"
_FILE_302AI_ENV = "302ai.env"
_FILE_ABACUS_ENV = "abacus.env"
_FILE_ALIBABA_CN_ENV = "alibaba-cn.env"
_FILE_ALIBABA_CODING_PLAN_ENV = "alibaba-coding-plan.env"
_FILE_ALIBABA_CODING_PLAN_CN_ENV = "alibaba-coding-plan-cn.env"
_FILE_ALIBABA_TOKEN_PLAN_ENV = "alibaba-token-plan.env"
_FILE_ALIBABA_TOKEN_PLAN_CN_ENV = "alibaba-token-plan-cn.env"
_FILE_AMAZON_BEDROCK_ENV = "amazon-bedrock.env"
_FILE_AMBIENT_ENV = "ambient.env"
_FILE_AMD_ENV = "amd.env"
_FILE_ANTHROPIC_ENV = "anthropic.env"
_FILE_ANYAPI_ENV = "anyapi.env"
_FILE_ARCEE_ENV = "arcee.env"
_FILE_ATOMIC_CHAT_ENV = "atomic-chat.env"
_FILE_AURIKO_ENV = "auriko.env"
_FILE_AZURE_ENV = "azure.env"
_FILE_AZURE_COGNITIVE_SERVICES_ENV = "azure-cognitive-services.env"
_FILE_BAILING_ENV = "bailing.env"
_FILE_BASETEN_ENV = "baseten.env"
_FILE_BERGET_ENV = "berget.env"
_FILE_BLUECLAW_ENV = "blueclaw.env"
_FILE_BOTHUB_ENV = "bothub.env"
_FILE_CEREBRAS_ENV = "cerebras.env"
_FILE_CHUTES_ENV = "chutes.env"
_FILE_CLARIFAI_ENV = "clarifai.env"
_FILE_CLAUDINIO_ENV = "claudinio.env"
_FILE_CLINE_PASS_ENV = "cline-pass.env"
_FILE_CLOUDFERRO_SHERLOCK_ENV = "cloudferro-sherlock.env"
_FILE_CLOUDFLARE_AI_GATEWAY_ENV = "cloudflare-ai-gateway.env"
_FILE_CLOUDFLARE_WORKERS_AI_ENV = "cloudflare-workers-ai.env"
_FILE_COHERE_ENV = "cohere.env"
_FILE_CORALBRICKS_ENV = "coralbricks.env"
_FILE_CORTECS_ENV = "cortecs.env"
_FILE_CROF_ENV = "crof.env"
_FILE_CROSSMODEL_ENV = "crossmodel.env"
_FILE_CRUSOE_ENV = "crusoe.env"
_FILE_DAOXE_ENV = "daoxe.env"
_FILE_DATABRICKS_ENV = "databricks.env"
_FILE_DEEPINFRA_ENV = "deepinfra.env"
_FILE_DEEPSEEK_ENV = "deepseek.env"
_FILE_DIGITALOCEAN_ENV = "digitalocean.env"
_FILE_DINFERENCE_ENV = "dinference.env"
_FILE_DRUN_ENV = "drun.env"
_FILE_EBCLOUD_ENV = "ebcloud.env"
_FILE_ECHO_ENV = "echo.env"
_FILE_EDENAI_ENV = "edenai.env"
_FILE_EMPIRIOLABS_ENV = "empiriolabs.env"
_FILE_EVROC_ENV = "evroc.env"
_FILE_FASTROUTER_ENV = "fastrouter.env"
_FILE_FIREWORKS_AI_ENV = "fireworks-ai.env"
_FILE_FREEMODEL_ENV = "freemodel.env"
_FILE_FRIENDLI_ENV = "friendli.env"
_FILE_FROGBOT_ENV = "frogbot.env"
_FILE_GITHUB_COPILOT_ENV = "github-copilot.env"
_FILE_GMICLOUD_ENV = "gmicloud.env"
_FILE_GOOGLE_ENV = "google.env"
_FILE_GOOGLE_VERTEX_ENV = "google-vertex.env"
_FILE_GOOGLE_VERTEX_ANTHROPIC_ENV = "google-vertex-anthropic.env"
_FILE_GREENPT_ENV = "greenpt.env"
_FILE_GROQ_ENV = "groq.env"
_FILE_HELICONE_ENV = "helicone.env"
_FILE_HETZNER_ENV = "hetzner.env"
_FILE_HPC_AI_ENV = "hpc-ai.env"
_FILE_HUGGINGFACE_ENV = "huggingface.env"
_FILE_HYPER_ENV = "hyper.env"
_FILE_IFLOWCN_ENV = "iflowcn.env"
_FILE_IMPOSSIBL_ENV = "impossibl.env"
_FILE_INCEPTION_ENV = "inception.env"
_FILE_INCEPTRON_ENV = "inceptron.env"
_FILE_INCO_ENV = "inco.env"
_FILE_INFER_ENV = "infer.env"
_FILE_INFERENCE_ENV = "inference.env"
_FILE_INFOMANIAK_ENV = "infomaniak.env"
_FILE_IO_NET_ENV = "io-net.env"
_FILE_ITERACOMPUTE_ENV = "iteracompute.env"
_FILE_JALAPENO_ENV = "jalapeno.env"
_FILE_JIEKOU_ENV = "jiekou.env"
_FILE_KLOKINTEGRATION_ENV = "klokintegration.env"
_FILE_KOSMIK_ENV = "kosmik.env"
_FILE_KUAE_CLOUD_CODING_PLAN_ENV = "kuae-cloud-coding-plan.env"
_FILE_LILAC_ENV = "lilac.env"
_FILE_LLAMA_ENV = "llama.env"
_FILE_LLMGATEWAY_ENV = "llmgateway.env"
_FILE_LLMGATEWAY_PROVIDERS_ENV = "llmgateway-providers.env"
_FILE_LLMTECH_ENV = "llmtech.env"
_FILE_LMSTUDIO_ENV = "lmstudio.env"
_FILE_LONGCAT_ENV = "longcat.env"
_FILE_LUCIDQUERY_ENV = "lucidquery.env"
_FILE_LYNKR_ENV = "lynkr.env"
_FILE_MEGANOVA_ENV = "meganova.env"
_FILE_MELIOUS_ENV = "melious.env"
_FILE_MERGE_GATEWAY_ENV = "merge-gateway.env"
_FILE_MINIMAX_ENV = "minimax.env"
_FILE_MINIMAX_CN_ENV = "minimax-cn.env"
_FILE_MINIMAX_CN_CODING_PLAN_ENV = "minimax-cn-coding-plan.env"
_FILE_MINIMAX_CODING_PLAN_ENV = "minimax-coding-plan.env"
_FILE_MISTRAL_ENV = "mistral.env"
_FILE_MIXLAYER_ENV = "mixlayer.env"
_FILE_MOARK_ENV = "moark.env"
_FILE_MODAL_ENV = "modal.env"
_FILE_MODEL_ORACLE_AI_ENV = "model-oracle-ai.env"
_FILE_MODELIS_ENV = "modelis.env"
_FILE_MODELSCOPE_ENV = "modelscope.env"
_FILE_MOONSHOTAI_ENV = "moonshotai.env"
_FILE_MOONSHOTAI_CN_ENV = "moonshotai-cn.env"
_FILE_MORPH_ENV = "morph.env"
_FILE_NAN_ENV = "nan.env"
_FILE_NEARAI_ENV = "nearai.env"
_FILE_NEBIUS_ENV = "nebius.env"
_FILE_NEON_ENV = "neon.env"
_FILE_NEOSMITH_ENV = "neosmith.env"
_FILE_NEURALWATT_ENV = "neuralwatt.env"
_FILE_NOVA_ENV = "nova.env"
_FILE_NOVITA_AI_ENV = "novita-ai.env"
_FILE_OCI_ENV = "oci.env"
_FILE_OFOX_ENV = "ofox.env"
_FILE_OLLAMA_CLOUD_ENV = "ollama-cloud.env"
_FILE_OPENAI_ENV = "openai.env"
_FILE_OPENCODE_GO_ENV = "opencode-go.env"
_FILE_OPENREASON_ENV = "openreason.env"
_FILE_OPPER_ENV = "opper.env"
_FILE_OVHCLOUD_ENV = "ovhcloud.env"
_FILE_PERPLEXITY_ENV = "perplexity.env"
_FILE_PERPLEXITY_AGENT_ENV = "perplexity-agent.env"
_FILE_PIONEER_ENV = "pioneer.env"
_FILE_POE_ENV = "poe.env"
_FILE_POOLSIDE_ENV = "poolside.env"
_FILE_PRIVATEMODE_AI_ENV = "privatemode-ai.env"
_FILE_QIHANG_AI_ENV = "qihang-ai.env"
_FILE_QINIU_AI_ENV = "qiniu-ai.env"
_FILE_QVAC_ENV = "qvac.env"
_FILE_REGOLO_AI_ENV = "regolo-ai.env"
_FILE_ROUTING_RUN_ENV = "routing-run.env"
_FILE_SAKANA_ENV = "sakana.env"
_FILE_SALAD_CLOUD_ENV = "salad-cloud.env"
_FILE_SAP_AI_CORE_ENV = "sap-ai-core.env"
_FILE_SARVAM_ENV = "sarvam.env"
_FILE_SCALEWAY_ENV = "scaleway.env"
_FILE_SCNET_TOKEN_PLAN_ENV = "scnet-token-plan.env"
_FILE_SCX_AI_ENV = "scx-ai.env"
_FILE_SENSENOVA_ENV = "sensenova.env"
_FILE_SILICONFLOW_ENV = "siliconflow.env"
_FILE_SILICONFLOW_CN_ENV = "siliconflow-cn.env"
_FILE_SNOWFLAKE_CORTEX_ENV = "snowflake-cortex.env"
_FILE_STACKIT_ENV = "stackit.env"
_FILE_STANDARDCOMPUTE_ENV = "standardcompute.env"
_FILE_STEPFUN_ENV = "stepfun.env"
_FILE_STEPFUN_AI_ENV = "stepfun-ai.env"
_FILE_STEPFUN_STEP_PLAN_ENV = "stepfun-step-plan.env"
_FILE_SUBCONSCIOUS_ENV = "subconscious.env"
_FILE_SUBMODEL_ENV = "submodel.env"
_FILE_SYNTHETIC_ENV = "synthetic.env"
_FILE_TEMPR_ENV = "tempr.env"
_FILE_TENCENT_CODING_PLAN_ENV = "tencent-coding-plan.env"
_FILE_TENCENT_TOKEN_PLAN_ENV = "tencent-token-plan.env"
_FILE_TENCENT_TOKENHUB_ENV = "tencent-tokenhub.env"
_FILE_TENSORX_ENV = "tensorx.env"
_FILE_THE_GRID_AI_ENV = "the-grid-ai.env"
_FILE_THINKINGMACHINES_ENV = "thinkingmachines.env"
_FILE_TINFOIL_ENV = "tinfoil.env"
_FILE_TOGETHERAI_ENV = "togetherai.env"
_FILE_TOKENGO_ENV = "tokengo.env"
_FILE_TRUSTEDROUTER_ENV = "trustedrouter.env"
_FILE_UMANS_AI_ENV = "umans-ai.env"
_FILE_UMANS_AI_CODING_PLAN_ENV = "umans-ai-coding-plan.env"
_FILE_UPSTAGE_ENV = "upstage.env"
_FILE_V0_ENV = "v0.env"
_FILE_VANCINE_ENV = "vancine.env"
_FILE_VENICE_ENV = "venice.env"
_FILE_VISPARK_ENV = "vispark.env"
_FILE_VIVGRID_ENV = "vivgrid.env"
_FILE_VOLCENGINE_ENV = "volcengine.env"
_FILE_VOLCENGINE_CODING_PLAN_ENV = "volcengine-coding-plan.env"
_FILE_VULTR_ENV = "vultr.env"
_FILE_WAFER_AI_ENV = "wafer.ai.env"
_FILE_WALLABY_ENV = "wallaby.env"
_FILE_WANDB_ENV = "wandb.env"
_FILE_WATSONX_ENV = "watsonx.env"
_FILE_XAI_ENV = "xai.env"
_FILE_XIAOMI_ENV = "xiaomi.env"
_FILE_XIAOMI_TOKEN_PLAN_AMS_ENV = "xiaomi-token-plan-ams.env"
_FILE_XIAOMI_TOKEN_PLAN_CN_ENV = "xiaomi-token-plan-cn.env"
_FILE_XIAOMI_TOKEN_PLAN_SGP_ENV = "xiaomi-token-plan-sgp.env"
_FILE_XPERSONA_ENV = "xpersona.env"
_FILE_ZAI_ENV = "zai.env"
_FILE_ZAI_CODING_PLAN_ENV = "zai-coding-plan.env"
_FILE_ZELDOC_ENV = "zeldoc.env"
_FILE_ZENIFRA_ENV = "zenifra.env"
_FILE_ZHIPUAI_ENV = "zhipuai.env"
_FILE_ZHIPUAI_CODING_PLAN_ENV = "zhipuai-coding-plan.env"
_FILE_INTERCEPT_YAML = "mcp-intercept.yaml"
_FILE_PER_MODEL_YAML = "per-model.yaml"
_FILE_USE_TOOL_HOOK = "use_tool_hook.py"

# Files whose change triggers a full env + Settings reload.
_ENV_FILES = {
    _FILE_PROXY_ENV,
    _FILE_OPENROUTER_ENV,
    _FILE_REQUESTY_ENV,
    _FILE_META_ENV,
    _FILE_NANO_GPT_ENV,
    _FILE_LLMTR_ENV,
    _FILE_KENARI_ENV,
    _FILE_UNOROUTER_ENV,
    _FILE_RUNINFRA_ENV,
    _FILE_NVIDIA_ENV,
    _FILE_OPENCODE_ENV,
    _FILE_ORCAROUTER_ENV,
    _FILE_AIHUBMIX_ENV,
    _FILE_TOKENROUTER_ENV,
    _FILE_INFERX_ENV,
    _FILE_KILO_ENV,
    _FILE_PENDRA_ENV,
    _FILE_VERCEL_ENV,
    _FILE_ZENMUX_ENV,
    _FILE_ABLITERATION_AI_ENV,
    _FILE_ABOVE_ENV,
    _FILE_AGENTROUTER_ENV,
    _FILE_AGNES_ENV,
    _FILE_AI_ROUTER_ENV,
    _FILE_AIAND_ENV,
    _FILE_AINETCAFE_ENV,
    _FILE_AIXY_ENV,
    _FILE_AKI_IO_ENV,
    _FILE_ALIBABA_ENV,
    _FILE_302AI_ENV,
    _FILE_ABACUS_ENV,
    _FILE_ALIBABA_CN_ENV,
    _FILE_ALIBABA_CODING_PLAN_ENV,
    _FILE_ALIBABA_CODING_PLAN_CN_ENV,
    _FILE_ALIBABA_TOKEN_PLAN_ENV,
    _FILE_ALIBABA_TOKEN_PLAN_CN_ENV,
    _FILE_AMAZON_BEDROCK_ENV,
    _FILE_AMBIENT_ENV,
    _FILE_AMD_ENV,
    _FILE_ANTHROPIC_ENV,
    _FILE_ANYAPI_ENV,
    _FILE_ARCEE_ENV,
    _FILE_ATOMIC_CHAT_ENV,
    _FILE_AURIKO_ENV,
    _FILE_AZURE_ENV,
    _FILE_AZURE_COGNITIVE_SERVICES_ENV,
    _FILE_BAILING_ENV,
    _FILE_BASETEN_ENV,
    _FILE_BERGET_ENV,
    _FILE_BLUECLAW_ENV,
    _FILE_BOTHUB_ENV,
    _FILE_CEREBRAS_ENV,
    _FILE_CHUTES_ENV,
    _FILE_CLARIFAI_ENV,
    _FILE_CLAUDINIO_ENV,
    _FILE_CLINE_PASS_ENV,
    _FILE_CLOUDFERRO_SHERLOCK_ENV,
    _FILE_CLOUDFLARE_AI_GATEWAY_ENV,
    _FILE_CLOUDFLARE_WORKERS_AI_ENV,
    _FILE_COHERE_ENV,
    _FILE_CORALBRICKS_ENV,
    _FILE_CORTECS_ENV,
    _FILE_CROF_ENV,
    _FILE_CROSSMODEL_ENV,
    _FILE_CRUSOE_ENV,
    _FILE_DAOXE_ENV,
    _FILE_DATABRICKS_ENV,
    _FILE_DEEPINFRA_ENV,
    _FILE_DEEPSEEK_ENV,
    _FILE_DIGITALOCEAN_ENV,
    _FILE_DINFERENCE_ENV,
    _FILE_DRUN_ENV,
    _FILE_EBCLOUD_ENV,
    _FILE_ECHO_ENV,
    _FILE_EDENAI_ENV,
    _FILE_EMPIRIOLABS_ENV,
    _FILE_EVROC_ENV,
    _FILE_FASTROUTER_ENV,
    _FILE_FIREWORKS_AI_ENV,
    _FILE_FREEMODEL_ENV,
    _FILE_FRIENDLI_ENV,
    _FILE_FROGBOT_ENV,
    _FILE_GITHUB_COPILOT_ENV,
    _FILE_GMICLOUD_ENV,
    _FILE_GOOGLE_ENV,
    _FILE_GOOGLE_VERTEX_ENV,
    _FILE_GOOGLE_VERTEX_ANTHROPIC_ENV,
    _FILE_GREENPT_ENV,
    _FILE_GROQ_ENV,
    _FILE_HELICONE_ENV,
    _FILE_HETZNER_ENV,
    _FILE_HPC_AI_ENV,
    _FILE_HUGGINGFACE_ENV,
    _FILE_HYPER_ENV,
    _FILE_IFLOWCN_ENV,
    _FILE_IMPOSSIBL_ENV,
    _FILE_INCEPTION_ENV,
    _FILE_INCEPTRON_ENV,
    _FILE_INCO_ENV,
    _FILE_INFER_ENV,
    _FILE_INFERENCE_ENV,
    _FILE_INFOMANIAK_ENV,
    _FILE_IO_NET_ENV,
    _FILE_ITERACOMPUTE_ENV,
    _FILE_JALAPENO_ENV,
    _FILE_JIEKOU_ENV,
    _FILE_KLOKINTEGRATION_ENV,
    _FILE_KOSMIK_ENV,
    _FILE_KUAE_CLOUD_CODING_PLAN_ENV,
    _FILE_LILAC_ENV,
    _FILE_LLAMA_ENV,
    _FILE_LLMGATEWAY_ENV,
    _FILE_LLMGATEWAY_PROVIDERS_ENV,
    _FILE_LLMTECH_ENV,
    _FILE_LMSTUDIO_ENV,
    _FILE_LONGCAT_ENV,
    _FILE_LUCIDQUERY_ENV,
    _FILE_LYNKR_ENV,
    _FILE_MEGANOVA_ENV,
    _FILE_MELIOUS_ENV,
    _FILE_MERGE_GATEWAY_ENV,
    _FILE_MINIMAX_ENV,
    _FILE_MINIMAX_CN_ENV,
    _FILE_MINIMAX_CN_CODING_PLAN_ENV,
    _FILE_MINIMAX_CODING_PLAN_ENV,
    _FILE_MISTRAL_ENV,
    _FILE_MIXLAYER_ENV,
    _FILE_MOARK_ENV,
    _FILE_MODAL_ENV,
    _FILE_MODEL_ORACLE_AI_ENV,
    _FILE_MODELIS_ENV,
    _FILE_MODELSCOPE_ENV,
    _FILE_MOONSHOTAI_ENV,
    _FILE_MOONSHOTAI_CN_ENV,
    _FILE_MORPH_ENV,
    _FILE_NAN_ENV,
    _FILE_NEARAI_ENV,
    _FILE_NEBIUS_ENV,
    _FILE_NEON_ENV,
    _FILE_NEOSMITH_ENV,
    _FILE_NEURALWATT_ENV,
    _FILE_NOVA_ENV,
    _FILE_NOVITA_AI_ENV,
    _FILE_OCI_ENV,
    _FILE_OFOX_ENV,
    _FILE_OLLAMA_CLOUD_ENV,
    _FILE_OPENAI_ENV,
    _FILE_OPENCODE_GO_ENV,
    _FILE_OPENREASON_ENV,
    _FILE_OPPER_ENV,
    _FILE_OVHCLOUD_ENV,
    _FILE_PERPLEXITY_ENV,
    _FILE_PERPLEXITY_AGENT_ENV,
    _FILE_PIONEER_ENV,
    _FILE_POE_ENV,
    _FILE_POOLSIDE_ENV,
    _FILE_PRIVATEMODE_AI_ENV,
    _FILE_QIHANG_AI_ENV,
    _FILE_QINIU_AI_ENV,
    _FILE_QVAC_ENV,
    _FILE_REGOLO_AI_ENV,
    _FILE_ROUTING_RUN_ENV,
    _FILE_SAKANA_ENV,
    _FILE_SALAD_CLOUD_ENV,
    _FILE_SAP_AI_CORE_ENV,
    _FILE_SARVAM_ENV,
    _FILE_SCALEWAY_ENV,
    _FILE_SCNET_TOKEN_PLAN_ENV,
    _FILE_SCX_AI_ENV,
    _FILE_SENSENOVA_ENV,
    _FILE_SILICONFLOW_ENV,
    _FILE_SILICONFLOW_CN_ENV,
    _FILE_SNOWFLAKE_CORTEX_ENV,
    _FILE_STACKIT_ENV,
    _FILE_STANDARDCOMPUTE_ENV,
    _FILE_STEPFUN_ENV,
    _FILE_STEPFUN_AI_ENV,
    _FILE_STEPFUN_STEP_PLAN_ENV,
    _FILE_SUBCONSCIOUS_ENV,
    _FILE_SUBMODEL_ENV,
    _FILE_SYNTHETIC_ENV,
    _FILE_TEMPR_ENV,
    _FILE_TENCENT_CODING_PLAN_ENV,
    _FILE_TENCENT_TOKEN_PLAN_ENV,
    _FILE_TENCENT_TOKENHUB_ENV,
    _FILE_TENSORX_ENV,
    _FILE_THE_GRID_AI_ENV,
    _FILE_THINKINGMACHINES_ENV,
    _FILE_TINFOIL_ENV,
    _FILE_TOGETHERAI_ENV,
    _FILE_TOKENGO_ENV,
    _FILE_TRUSTEDROUTER_ENV,
    _FILE_UMANS_AI_ENV,
    _FILE_UMANS_AI_CODING_PLAN_ENV,
    _FILE_UPSTAGE_ENV,
    _FILE_V0_ENV,
    _FILE_VANCINE_ENV,
    _FILE_VENICE_ENV,
    _FILE_VISPARK_ENV,
    _FILE_VIVGRID_ENV,
    _FILE_VOLCENGINE_ENV,
    _FILE_VOLCENGINE_CODING_PLAN_ENV,
    _FILE_VULTR_ENV,
    _FILE_WAFER_AI_ENV,
    _FILE_WALLABY_ENV,
    _FILE_WANDB_ENV,
    _FILE_WATSONX_ENV,
    _FILE_XAI_ENV,
    _FILE_XIAOMI_ENV,
    _FILE_XIAOMI_TOKEN_PLAN_AMS_ENV,
    _FILE_XIAOMI_TOKEN_PLAN_CN_ENV,
    _FILE_XIAOMI_TOKEN_PLAN_SGP_ENV,
    _FILE_XPERSONA_ENV,
    _FILE_ZAI_ENV,
    _FILE_ZAI_CODING_PLAN_ENV,
    _FILE_ZELDOC_ENV,
    _FILE_ZENIFRA_ENV,
    _FILE_ZHIPUAI_ENV,
    _FILE_ZHIPUAI_CODING_PLAN_ENV,
}

# Files whose content is embedded in settings (path stored in proxy.env).
_SETTING_BACKED_FILES = {_FILE_INTERCEPT_YAML, _FILE_PER_MODEL_YAML, _FILE_USE_TOOL_HOOK}


class ConfigReloader:
    """Polls config files and triggers reload callbacks on change.

    Lifecycle: ``start()`` launches a background task; ``stop()`` cancels it.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._mtimes: Dict[str, float] = {}
        self._app: Optional[Any] = None

        # Callbacks set by the application during wiring.
        self._on_env_reload: Optional[Callable[[], Awaitable[None]]] = None
        self._on_intercept_reload: Optional[Callable[[], Awaitable[None]]] = None
        self._on_per_model_reload: Optional[Callable[[], Awaitable[None]]] = None
        self._on_tool_hook_reload: Optional[Callable[[], Awaitable[None]]] = None

    def attach(
        self,
        app: Any,
        on_env_reload: Callable[[], Awaitable[None]],
        on_intercept_reload: Callable[[], Awaitable[None]],
        on_per_model_reload: Callable[[], Awaitable[None]],
        on_tool_hook_reload: Callable[[], Awaitable[None]],
    ) -> None:
        """Wire the reloader to application lifecycle callbacks."""
        self._app = app
        self._on_env_reload = on_env_reload
        self._on_intercept_reload = on_intercept_reload
        self._on_per_model_reload = on_per_model_reload
        self._on_tool_hook_reload = on_tool_hook_reload

    def start(self) -> None:
        """Launch the background polling task."""
        if self._task is not None:
            return
        self._init_mtimes()
        self._task = asyncio.ensure_future(self._poll_loop())
        LOGGER.info("ConfigReloader started (poll interval=%.1fs)", _POLL_INTERVAL)

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        LOGGER.info("ConfigReloader stopped")

    # ------------------------------------------------------------------
    # Internal: file tracking
    # ------------------------------------------------------------------

    def _config_path(self, filename: str) -> Path:
        return Path(CONFIG_DIR) / filename

    def _file_exists(self, filename: str) -> bool:
        return self._config_path(filename).is_file()

    def _get_mtime(self, filename: str) -> Optional[float]:
        try:
            return os.path.getmtime(self._config_path(filename))
        except OSError:
            return None

    def _init_mtimes(self) -> None:
        """Snapshot current mtimes of all watched files."""
        for fname in self._all_watched_files():
            self._mtimes[fname] = self._get_mtime(fname) or 0.0

    def _all_watched_files(self) -> Set[str]:
        return _ENV_FILES | _SETTING_BACKED_FILES

    def _detect_changes(self) -> Set[str]:
        """Return the set of filenames whose mtime increased since the last check."""
        changed: Set[str] = set()
        for fname in self._all_watched_files():
            current = self._get_mtime(fname)
            if current is None:
                continue
            if current > self._mtimes.get(fname, 0.0):
                changed.add(fname)
                self._mtimes[fname] = current
        return changed

    # ------------------------------------------------------------------
    # Internal: reload cascade
    # ------------------------------------------------------------------

    async def _handle_changes(self, changed: Set[str]) -> None:
        """Run the selective reload cascade for the given changed files."""
        if not changed:
            return

        LOGGER.info("ConfigReloader: detected changes: %s", ", ".join(sorted(changed)))

        # Step 1: If any env file changed, delegate to the env reload callback
        # which handles the full cascade (env -> Settings -> all components).
        env_changed = changed & _ENV_FILES
        if env_changed:
            await self._safe_call(self._on_env_reload, "env reload")
            # env reload callback handles the full cascade, so we're done.
            return

        # Step 2: Handle direct file changes (not via settings).
        if _FILE_INTERCEPT_YAML in changed:
            await self._safe_call(self._on_intercept_reload, "intercept reload")
        if _FILE_PER_MODEL_YAML in changed:
            await self._safe_call(self._on_per_model_reload, "per-model reload")
        if _FILE_USE_TOOL_HOOK in changed:
            await self._safe_call(self._on_tool_hook_reload, "tool-hook reload")

    async def _safe_call(
        self,
        callback: Optional[Callable[[], Awaitable[None]]],
        label: str,
    ) -> None:
        """Call an async callback, logging errors instead of propagating."""
        if callback is None:
            return
        try:
            await callback()
        except Exception as exc:
            LOGGER.error("ConfigReloader: %s failed: %s", label, exc)

    # ------------------------------------------------------------------
    # Internal: polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Main polling loop — runs until cancelled."""
        while True:
            try:
                changed = self._detect_changes()
                if changed:
                    await self._handle_changes(changed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("ConfigReloader: poll loop error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Application-level callback factories
# ---------------------------------------------------------------------------


async def reload_per_model_config(app: Any) -> None:
    """Reload per-model config and update app["per_model_config"]."""
    try:
        new_config = load_per_model_config()
        app["per_model_config"] = new_config
        LOGGER.info("Per-model config reloaded: %d entries", len(new_config))
    except Exception as exc:
        LOGGER.error("Per-model config reload failed: %s", exc)


async def reload_tool_hook(app: Any) -> None:
    """Update ToolHookGateway.enabled to match current settings."""
    hook_gateway = app.get("hook_gateway")
    if hook_gateway is None:
        return
    new_enabled = bool(settings.use_tool_hook)
    if hook_gateway.enabled != new_enabled:
        LOGGER.info("Tool hook %s", "enabled" if new_enabled else "disabled")
    hook_gateway.enabled = new_enabled


async def reload_intercept(app: Any) -> None:
    """Restart the MCPInterceptor with the latest config.

    The old interceptor is stopped first, then a new one is created and
    started. If the new config is invalid or the MCP server fails to start,
    the old interceptor is kept running.
    """
    old_intercept: Optional[MCPInterceptor] = app.get("mcp_intercept")
    if old_intercept is not None:
        await old_intercept.stop()

    try:
        new_config = load_intercept_config()
    except Exception as exc:
        LOGGER.error("Intercept config reload failed, disabling intercept: %s", exc)
        new_config = None

    new_intercept = MCPInterceptor(new_config)
    app["mcp_intercept"] = new_intercept

    if new_intercept.enabled:
        try:
            await new_intercept.start()
            LOGGER.info("MCP interceptor reloaded and started")
        except Exception as exc:
            LOGGER.error("MCP interceptor start failed after reload: %s", exc)


async def reload_env_and_propagate(app: Any) -> None:
    """Reload env files + Settings, then propagate to all dependent components.

    This is the full cascade: env -> Settings -> per-model / tool-hook / intercept.
    """
    credits_checker = app.get("credits_checker")

    try:
        reload_settings()
    except Exception as exc:
        LOGGER.error("Env reload failed, keeping previous settings: %s", exc)
        return

    await reload_per_model_config(app)
    await reload_tool_hook(app)
    await reload_intercept(app)

    # Notify credits checker that the provider may have changed.
    if credits_checker is not None:
        credits_checker.on_provider_changed()
