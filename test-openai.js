
require("dotenv").config();

const OpenAI = require("openai");

if (!process.env.OPENAI_API_KEY) {
    console.error("ERROR: OPENAI_API_KEY is missing from .env");
    process.exit(1);
}

const client = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY
});

async function main() {
    try {
        console.log("Connecting to OpenAI...");

        const response = await client.responses.create({
            model: process.env.OPENAI_MODEL || "gpt-5.4-mini",
            input: "Say hello to ConvoGen AI in one sentence."
        });

        console.log("\nAI response:");
        console.log(response.output_text);

        console.log("\nOpenAI connection successful!");
    } catch (error) {
        console.error("\nOpenAI error:", error.message);
    }
}

main();
