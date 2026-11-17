
require("dotenv").config();

const Groq = require("groq-sdk");

async function main() {
    const groq = new Groq({
        apiKey: process.env.GROQ_API_KEY
    });

    const response = await groq.chat.completions.create({
        model: process.env.GROQ_MODEL || "qwen/qwen3.8-27b",
        messages: [
            {
                role: "user",
                content: "Say hello to ConvoGen AI in one sentence."
            }
        ],
        max_tokens: 100
    });

    console.log("\nGroq response:\n");
    console.log(response.choices[0].message.content);
}

main().catch(error => {
    console.error("Groq test failed:", error.message);
    process.exitCode = 1;
});
