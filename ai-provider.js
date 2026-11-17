
"use strict";

const OpenAI = require("openai");
const Groq = require("groq-sdk");

const INSTRUCTIONS = `
You are ConvoGen AI, a helpful conversational assistant.

- Answer clearly and accurately.
- Use Markdown for headings, lists and code.
- Do not claim to have searched the web unless a search was actually performed.
- If you are unsure, say so.
- Keep answers appropriately concise.
`;

const PROVIDER_CONFIG = {
    openai: {
        label: "OpenAI",
        envKey: "OPENAI_API_KEY",
        modelEnv: "OPENAI_MODEL",
        defaultModel: "gpt-5.4"
    },
    gemini: {
        label: "Google Gemini",
        envKey: "GEMINI_API_KEY",
        modelEnv: "GEMINI_MODEL",
        defaultModel: "gemini-2.5-flash"
    },
    groq: {
        label: "Groq",
        envKey: "GROQ_API_KEY",
        modelEnv: "GROQ_MODEL",
        defaultModel: "llama-3.3-70b-versatile"
    },
    openrouter: {
        label: "OpenRouter",
        envKey: "OPENROUTER_API_KEY",
        modelEnv: "OPENROUTER_MODEL",
        defaultModel: "openrouter/free"
    }
};

function getAvailableModels() {
    const models = [];

    for (const [provider, config] of Object.entries(PROVIDER_CONFIG)) {
        if (!process.env[config.envKey]) {
            continue;
        }

        const model = process.env[config.modelEnv] || config.defaultModel;

        models.push({
            id: `${provider}:${model}`,
            provider,
            providerLabel: config.label,
            model,
            label: `${config.label} — ${model}`
        });
    }

    return models;
}

function parseModelId(modelId) {
    const value = String(modelId || "").trim();
    const separator = value.indexOf(":");

    if (separator < 1) {
        throw new Error("Select a valid AI model.");
    }

    const provider = value.slice(0, separator);
    const model = value.slice(separator + 1);

    if (!PROVIDER_CONFIG[provider] || !model) {
        throw new Error("The selected AI model is not supported.");
    }

    const config = PROVIDER_CONFIG[provider];

    if (!process.env[config.envKey]) {
        throw new Error(`${config.label} is not configured on the server.`);
    }

    const allowed = getAvailableModels().some(
        item => item.id === value
    );

    if (!allowed) {
        throw new Error("This model is not currently available.");
    }

    return { provider, model };
}

function normalizeMessages(messages) {
    return [
        {
            role: "system",
            content: INSTRUCTIONS
        },
        ...messages.map(message => ({
            role: message.role === "assistant" ? "assistant" : "user",
            content: String(message.content || "")
        }))
    ];
}

async function generateOpenAI(model, messages) {
    const client = new OpenAI({
        apiKey: process.env.OPENAI_API_KEY
    });

    const response = await client.responses.create({
        model,
        instructions: INSTRUCTIONS,
        input: messages.map(message => ({
            role: message.role,
            content: message.content
        })),
        max_output_tokens: 1500,
        store: false
    });

    return {
        text: String(response.output_text || "").trim(),
        inputTokens: response.usage?.input_tokens || 0,
        outputTokens: response.usage?.output_tokens || 0
    };
}

async function generateGemini(model, messages) {
    const { GoogleGenAI } = await import("@google/genai");

    const client = new GoogleGenAI({
        apiKey: process.env.GEMINI_API_KEY
    });

    const contents = messages.map(message => ({
        role: message.role === "assistant" ? "model" : "user",
        parts: [{ text: message.content }]
    }));

    const response = await client.models.generateContent({
        model,
        contents,
        config: {
            systemInstruction: INSTRUCTIONS,
            maxOutputTokens: 1500
        }
    });

    return {
        text: String(response.text || "").trim(),
        inputTokens: response.usageMetadata?.promptTokenCount || 0,
        outputTokens: response.usageMetadata?.candidatesTokenCount || 0
    };
}

async function generateGroq(model, messages) {
    const client = new Groq({
        apiKey: process.env.GROQ_API_KEY
    });

    const response = await client.chat.completions.create({
        model,
        messages: normalizeMessages(messages),
        max_tokens: 1500,
        temperature: 0.7
    });

    return {
        text: String(
            response.choices?.[0]?.message?.content || ""
        ).trim(),
        inputTokens: response.usage?.prompt_tokens || 0,
        outputTokens: response.usage?.completion_tokens || 0
    };
}

async function generateOpenRouter(model, messages) {
    const client = new OpenAI({
        apiKey: process.env.OPENROUTER_API_KEY,
        baseURL: "https://openrouter.ai/api/v1",
        defaultHeaders: {
            "HTTP-Referer": process.env.APP_URL || "http://localhost:3000",
            "X-Title": "ConvoGen AI"
        }
    });

    const response = await client.chat.completions.create({
        model,
        messages: normalizeMessages(messages),
        max_tokens: 1500,
        temperature: 0.7
    });

    return {
        text: String(
            response.choices?.[0]?.message?.content || ""
        ).trim(),
        inputTokens: response.usage?.prompt_tokens || 0,
        outputTokens: response.usage?.completion_tokens || 0
    };
}

async function generateResponse(modelId, messages) {
    const { provider, model } = parseModelId(modelId);

    let result;

    switch (provider) {
        case "openai":
            result = await generateOpenAI(model, messages);
            break;

        case "gemini":
            result = await generateGemini(model, messages);
            break;

        case "groq":
            result = await generateGroq(model, messages);
            break;

        case "openrouter":
            result = await generateOpenRouter(model, messages);
            break;

        default:
            throw new Error("Unsupported AI provider.");
    }

    if (!result.text) {
        throw new Error(
            `${PROVIDER_CONFIG[provider].label} returned an empty response.`
        );
    }

    return {
        ...result,
        provider,
        model
    };
}

module.exports = {
    getAvailableModels,
    generateResponse
};
