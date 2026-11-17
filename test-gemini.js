
require("dotenv").config();

async function main() {
    const { GoogleGenAI } = await import("@google/genai");

    const client = new GoogleGenAI({
        apiKey: process.env.GEMINI_API_KEY
    });

    const response = await client.models.generateContent({
        model: process.env.GEMINI_MODEL || "gemini-3.8-flash",
        contents: "Say hello to ConvoGen AI in one sentence."
    });

    console.log("\nGemini response:\n");
    console.log(response.text);
}

main().catch(error => {
    console.error("Gemini test failed:", error.message);
    process.exitCode = 1;
});
