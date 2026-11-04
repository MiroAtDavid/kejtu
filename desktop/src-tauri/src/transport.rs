use reqwest::{redirect::Policy, Client};
use serde_json::Value;
use std::time::Duration;

use crate::wire::{ValidatedResponse, RESPONSE_BYTES};

pub async fn exchange(port: u16, token: &str, request: &Value) -> Result<Value, String> {
    let request_id = request
        .get("request_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "invalid native request identity".to_string())?;
    let client = Client::builder()
        .redirect(Policy::none())
        .no_proxy()
        .timeout(Duration::from_secs(match request["operation"].as_str() {
            Some("models.key") => 30,     // Desktop keyring may request an unlock.
            Some("skills.import") => 40,  // Import IO is bounded to 35 seconds.
            Some("mcp.inspect") => 35,    // SDK discovery has a 30-second bound.
            Some("models.chatgpt") => 45, // Bounded discovery and session revocation.
            Some("models.check")
                if request
                    .pointer("/arguments/profile/provider")
                    .and_then(serde_json::Value::as_str)
                    == Some("chatgpt") =>
            {
                75
            }
            Some("models.check") => 8, // Metadata adapter has a five-second bound.
            _ => 5,
        }))
        .build()
        .map_err(|_| "native transport unavailable".to_string())?;
    let mut response = client
        .post(format!("http://127.0.0.1:{port}/native/v1"))
        .header("Authorization", format!("Bearer {token}"))
        .header("Content-Type", "application/json")
        .json(request)
        .send()
        .await
        .map_err(|_| "native transport unavailable".to_string())?;
    if response
        .content_length()
        .is_some_and(|size| size > RESPONSE_BYTES as u64)
    {
        return Err("native response exceeded its byte bound".into());
    }
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| "native response unavailable".to_string())?
    {
        if body.len() + chunk.len() > RESPONSE_BYTES {
            return Err("native response exceeded its byte bound".into());
        }
        body.extend_from_slice(&chunk);
    }
    let value: Value =
        serde_json::from_slice(&body).map_err(|_| "native response was malformed".to_string())?;
    if value.get("request_id").and_then(Value::as_str) != Some(request_id) {
        return Err("native response identity was incompatible".into());
    }
    ValidatedResponse::parse(
        request["operation"].as_str().ok_or("operation missing")?,
        value,
    )
    .map(ValidatedResponse::into_value)
}
