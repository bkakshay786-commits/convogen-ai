
require("dotenv").config();

const Groq = require("groq-sdk");

async function main() {
    if (!process.env.GROQ_API_KEY) {
        throw new Error("GROQ_API_KEY is missing from .env");
    }

    const groq = new Groq({
        apiKey: process.env.GROQ_API_KEY
    });

    const result = await groq.models.list();

    console.log("\nAvailable Groq models:\n");

    for (const model of result.data) {
        if (model.active !== false) {
            console.log(model.id);
        }
    }
}

main().catch(error => {
    console.error("Groq model discovery failed:", error.message);
    process.exitCode = 1;
});
