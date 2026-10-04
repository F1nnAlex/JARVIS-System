'use strict';

// JARVIS's "brain": a streaming conversation with Claude.
// Runs in Electron's main process so the API key never reaches the window.

const Anthropic = require('@anthropic-ai/sdk');

const MAX_HISTORY_MESSAGES = 40;
const MAX_CONTINUATIONS = 3;

const PERSONA = `You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), the personal AI assistant from the Iron Man films, now running as a desktop app on the user's computer.

Personality: calm, composed, impeccably polite, with a dry British wit. You are loyal and genuinely helpful, and you occasionally offer a gently sardonic aside, but you never let humour get in the way of an answer.

Everything you write is converted to speech and spoken aloud, so:
- Answer in plain spoken English. Never use markdown, bullet points, numbered lists, headings, tables, code blocks, emoji or URLs.
- Keep replies short: usually one to three sentences. Go longer only when the user explicitly asks for detail, and even then speak in natural paragraphs.
- Write numbers, units and symbols the way a person would say them (for example "twenty-two degrees", "about three point five kilometres", "five past nine").
- Lead with the answer. Do not repeat the question back.
- Speech recognition can mishear words. If a request seems garbled, make the most sensible interpretation, or briefly ask the user to repeat it.

When the user asks about current events, live information, or anything you are unsure of and a web search tool is available, use it, then give a brief spoken summary without reading out sources or links.

You cannot control the user's computer, smart home devices, or other apps yet. If asked to, say so briefly and offer what you can do instead.`;

class Brain {
  constructor({ getApiKey, getSettings }) {
    this.getApiKey = getApiKey;
    this.getSettings = getSettings;
    this.history = [];
    this.active = null;
    this.webSearchDisabledForSession = false;
  }

  reset() {
    this.cancel();
    this.history = [];
  }

  cancel() {
    if (this.active) {
      this.active.abort();
      this.active = null;
    }
  }

  // Stream a reply to `text`. `emit` receives {type: 'status'|'text'|'done'|'error', ...}.
  async ask(text, context, emit) {
    this.cancel();
    const apiKey = this.getApiKey();
    if (!apiKey) {
      emit({ type: 'error', code: 'no_api_key', message: 'No Anthropic API key is configured.' });
      return;
    }

    const controller = new AbortController();
    this.active = controller;
    const client = new Anthropic({ apiKey });
    const settings = this.getSettings();

    this.history.push({ role: 'user', content: text });
    let reply = '';

    try {
      emit({ type: 'status', status: 'thinking' });
      reply = await this.#streamReply(client, settings, context, controller.signal, emit, true);
    } catch (err) {
      if (err instanceof Anthropic.BadRequestError && !controller.signal.aborted) {
        // A newer request feature (web search, fallbacks, mid-conversation
        // system messages) may not be enabled for this account. Retry once
        // with a minimal request so the user still gets an answer.
        console.warn('[brain] request rejected, retrying with a minimal request:', err.message);
        this.webSearchDisabledForSession = true;
        try {
          reply = await this.#streamReply(client, settings, context, controller.signal, emit, false);
        } catch (retryErr) {
          this.#handleError(retryErr, controller, emit);
        }
      } else {
        this.#handleError(err, controller, emit);
      }
    }

    if (reply.trim()) {
      this.history.push({ role: 'assistant', content: reply });
    } else {
      this.history.pop(); // keep user/assistant turns alternating
    }
    if (this.history.length > MAX_HISTORY_MESSAGES) {
      this.history.splice(0, this.history.length - MAX_HISTORY_MESSAGES);
    }
    if (this.active === controller) this.active = null;
    emit({ type: 'done', aborted: controller.signal.aborted });
  }

  async #streamReply(client, settings, context, signal, emit, fullFeatures) {
    const contextNote = buildContextNote(settings, context);
    const useWebSearch = fullFeatures && settings.webSearch && !this.webSearchDisabledForSession;

    let messages;
    if (fullFeatures) {
      // Volatile details (time, weather) go in a trailing system message so the
      // persona and earlier conversation stay identical and cacheable.
      messages = [...this.history, { role: 'system', content: contextNote }];
    } else {
      const last = this.history[this.history.length - 1];
      messages = [
        ...this.history.slice(0, -1),
        { role: 'user', content: `${last.content}\n\n(${contextNote})` },
      ];
    }

    const params = {
      model: settings.model || 'claude-opus-5-5',
      max_tokens: 4096,
      system: PERSONA,
      messages,
      output_config: { effort: settings.effort || 'low' },
    };
    if (fullFeatures) {
      params.cache_control = { type: 'ephemeral' };
      params.betas = ['server-side-fallback-2026-07-01'];
      params.fallbacks = 'default';
    }
    if (useWebSearch) {
      params.tools = [{ type: 'web_search_20260209', name: 'web_search', max_uses: 3 }];
    }

    let reply = '';
    for (let round = 0; round <= MAX_CONTINUATIONS; round++) {
      const stream = fullFeatures
        ? client.beta.messages.stream(params, { signal })
        : client.messages.stream(params, { signal });

      for await (const event of stream) {
        if (event.type === 'content_block_start') {
          const blockType = event.content_block.type;
          if (blockType === 'server_tool_use') emit({ type: 'status', status: 'searching' });
          if (blockType === 'text') emit({ type: 'status', status: 'speaking' });
        } else if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
          reply += event.delta.text;
          emit({ type: 'text', text: event.delta.text });
        }
      }

      const message = await stream.finalMessage();
      if (message.stop_reason === 'pause_turn') {
        // A long server-side search paused the turn: hand it back to continue.
        params.messages = [...params.messages, { role: 'assistant', content: message.content }];
        continue;
      }
      if (message.stop_reason === 'refusal' && !reply.trim()) {
        const line = "I'm afraid that's not something I can help with.";
        reply = line;
        emit({ type: 'text', text: line });
      }
      break;
    }
    return reply;
  }

  #handleError(err, controller, emit) {
    if (controller.signal.aborted || err instanceof Anthropic.APIUserAbortError) return;
    console.error('[brain] request failed:', err);
    let code = 'unknown';
    let message = err.message || String(err);
    if (err instanceof Anthropic.AuthenticationError) {
      code = 'auth';
      message = 'The API key was rejected.';
    } else if (err instanceof Anthropic.PermissionDeniedError) {
      code = 'permission';
    } else if (err instanceof Anthropic.NotFoundError) {
      code = 'not_found';
      message = 'That model was not found. Check the model name in settings.';
    } else if (err instanceof Anthropic.RateLimitError) {
      code = 'rate_limit';
      message = 'Rate limited by the API.';
    } else if (err instanceof Anthropic.APIConnectionError) {
      code = 'network';
      message = 'Could not reach the Claude API.';
    } else if (err instanceof Anthropic.APIError) {
      code = `http_${err.status}`;
    }
    emit({ type: 'error', code, message });
  }
}

function buildContextNote(settings, context = {}) {
  const parts = [];
  if (context.localTime) parts.push(`Current local date and time: ${context.localTime}.`);
  if (settings.userName) parts.push(`The user's name is ${settings.userName}.`);
  if (settings.addressAs) {
    parts.push(`Address the user as "${settings.addressAs}" where it sounds natural.`);
  }
  if (settings.location) parts.push(`The user is in ${settings.location}.`);
  if (context.weather) parts.push(`Current weather there: ${context.weather}.`);
  if (context.inputMode === 'voice') {
    parts.push('This message came from speech recognition.');
  }
  return parts.join(' ') || 'No additional context.';
}

module.exports = { Brain, PERSONA, buildContextNote };
