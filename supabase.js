
require("dotenv").config();

const { createClient } = require("@supabase/supabase-js");

// ==========================================
// 1. READ ENVIRONMENT VARIABLES
// ==========================================

const SUPABASE_URL = process.env.SUPABASE_URL;

const SUPABASE_PUBLISHABLE_KEY =
    process.env.SUPABASE_PUBLISHABLE_KEY;

// ==========================================
// 2. VALIDATE CONFIGURATION
// ==========================================

if (!SUPABASE_URL) {
    throw new Error(
        "SUPABASE_URL is missing from your .env file!"
    );
}

if (!SUPABASE_PUBLISHABLE_KEY) {
    throw new Error(
        "SUPABASE_PUBLISHABLE_KEY is missing from your .env file!"
    );
}

// ==========================================
// 3. CREATE SUPABASE CLIENT
// ==========================================

const supabase = createClient(
    SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY,
    {
        auth: {
            autoRefreshToken: false,
            persistSession: false,
            detectSessionInUrl: false
        }
    }
);

// ==========================================
// 4. EXPORT SUPABASE CLIENT
// ==========================================

module.exports = supabase;
