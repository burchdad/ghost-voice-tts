/*
 * K6 Load Testing Script for Ghost Voice TTS
 * 
 * Usage:
 *   k6 run loadtest.js                    # Default: minimal load
 *   k6 run -v loadtest.js                 # Verbose output
 *   k6 run --vus 50 --duration 5m loadtest.js  # 50 users, load test, 5 min
 *   k6 run --stage 2m:10 --stage 5m:50 --stage 2m:0 loadtest.js  # Ramp up
 * 
 * Scenarios:
 * 1. Register and authenticate
 * 2. Synthesize text
 * 3. Batch synthesis
 * 4. Voice operations
 * 5. Marketplace operations
 */

import http from 'k6/http';
import { check, group, sleep, fail } from 'k6';
import { Rate, Trend, Gauge, Counter } from 'k6/metrics';

// Custom metrics
const registrationDuration = new Trend('registration_duration');
const synthesisLatency = new Trend('synthesis_latency');
const batchSynthesisLatency = new Trend('batch_synthesis_latency');
const voiceOperationLatency = new Trend('voice_operation_latency');
const rateLimit429s = new Counter('rate_limit_429_count');
const successfulRequests = new Counter('successful_requests_total');

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const USERS = parseInt(__ENV.USERS || '10');
const DURATION = __ENV.DURATION || '2m';
const THINK_TIME = parseInt(__ENV.THINK_TIME || '2');

// Test configuration
export const options = {
  scenarios: {
    basic_load: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '1m', target: USERS },
        { duration: DURATION, target: USERS },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    'http_req_duration': ['p(95)<500', 'p(99)<2000'],
    'http_req_failed': ['rate<0.1'],
    'registration_duration': ['p(95)<2000'],
    'synthesis_latency': ['p(95)<3000'],
  },
};

// Shared test data
const voices = [];
let tokens = {};

export default function () {
  // Each VU gets unique credentials
  const userId = `user-${__VU}-${__ITER}`;
  const email = `${userId}@loadtest.ghost`;
  const password = 'TestPassword123!';

  group('Authentication Flow', () => {
    // Register user
    let registerResponse = http.post(`${BASE_URL}/auth/register`, {
      username: userId,
      email: email,
      password: password,
    });

    registrationDuration.add(registerResponse.timings.duration);

    check(registerResponse, {
      'registration status is 200': (r) => r.status === 200,
      'registration returns token': (r) => r.json('access_token') !== undefined,
    }) || fail('Registration failed');

    tokens[userId] = registerResponse.json('access_token');
    successfulRequests.add(1);

    sleep(THINK_TIME);

    // Get user info
    let meResponse = http.get(`${BASE_URL}/me`, {
      headers: {
        Authorization: `Bearer ${tokens[userId]}`,
      },
    });

    check(meResponse, {
      'get user status is 200': (r) => r.status === 200,
      'user has quota': (r) => r.json('monthly_synthesis_quota') > 0,
    });

    successfulRequests.add(1);
    sleep(THINK_TIME);
  });

  group('Voice Operations', () => {
    const token = tokens[userId];

    // Create voice
    let voiceResponse = http.post(
      `${BASE_URL}/voices/create`,
      {
        name: `TestVoice-${__VU}-${__ITER}`,
        description: 'Load test voice',
        gender: 'neutral',
        language: 'en',
      },
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    );

    voiceOperationLatency.add(voiceResponse.timings.duration);

    if (voiceResponse.status === 200) {
      const voiceId = voiceResponse.json('id');
      voices.push(voiceId);
      successfulRequests.add(1);

      // Get voice details
      let voiceDetailsResponse = http.get(`${BASE_URL}/voices/${voiceId}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      check(voiceDetailsResponse, {
        'get voice details status is 200': (r) => r.status === 200,
      });

      successfulRequests.add(1);
      sleep(THINK_TIME);
    }
  });

  group('Synthesis Operations', () => {
    const token = tokens[userId];

    if (voices.length === 0) {
      console.log('Skipping synthesis - no voices created');
      return;
    }

    const voiceId = voices[0];
    const texts = [
      'Hello world, this is a test synthesis.',
      'The quick brown fox jumps over the lazy dog.',
      'How are you doing today?',
    ];

    // Single synthesis
    group('Single Synthesis', () => {
      let synthResponse = http.post(
        `${BASE_URL}/synthesize`,
        {
          text: texts[Math.floor(Math.random() * texts.length)],
          voice_id: voiceId,
          language: 'en',
          style: 'normal',
        },
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      synthesisLatency.add(synthResponse.timings.duration);

      check(synthResponse, {
        'synthesis status is 200 or 429': (r) => r.status === 200 || r.status === 429,
        'synthesis returns job id': (r) => r.json('id') !== undefined || r.status === 429,
      });

      if (synthResponse.status === 429) {
        rateLimit429s.add(1);
      } else {
        successfulRequests.add(1);
      }

      sleep(THINK_TIME);
    });

    // Batch synthesis
    group('Batch Synthesis', () => {
      let batchPayload = {
        voice_id: voiceId,
        items: texts.map((text, idx) => ({
          text: text,
          language: 'en',
          style: 'normal',
        })),
      };

      let batchResponse = http.post(`${BASE_URL}/synthesize-batch`, batchPayload, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      batchSynthesisLatency.add(batchResponse.timings.duration);

      check(batchResponse, {
        'batch synthesis status is 200 or 429': (r) => r.status === 200 || r.status === 429,
        'batch returns batch_id': (r) => r.json('batch_id') !== undefined || r.status === 429,
      });

      if (batchResponse.status === 429) {
        rateLimit429s.add(1);
      } else {
        successfulRequests.add(1);
      }

      sleep(THINK_TIME);
    });
  });

  group('Quota Operations', () => {
    const token = tokens[userId];

    // Check quota
    let quotaResponse = http.get(`${BASE_URL}/me/quota`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    check(quotaResponse, {
      'quota endpoint status is 200': (r) => r.status === 200,
      'quota has usage info': (r) => r.json('current_month_usage') !== undefined,
    });

    successfulRequests.add(1);
    sleep(THINK_TIME);

    // Pre-check quota
    let preCheckResponse = http.post(`${BASE_URL}/quota/check`, {
      text_length: 100,
    });

    check(preCheckResponse, {
      'pre-check quota status is 200': (r) => r.status === 200,
    });

    successfulRequests.add(1);
    sleep(THINK_TIME);
  });

  group('Marketplace Operations', () => {
    const token = tokens[userId];

    // Get marketplace stats
    let statsResponse = http.get(`${BASE_URL}/marketplace/stats`);

    check(statsResponse, {
      'marketplace stats status is 200': (r) => r.status === 200,
    });

    successfulRequests.add(1);
    sleep(THINK_TIME);

    // Get free trial status
    let trialResponse = http.get(`${BASE_URL}/me/free-trial`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    check(trialResponse, {
      'free trial status is 200': (r) => r.status === 200,
    });

    successfulRequests.add(1);
    sleep(THINK_TIME);
  });
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
    'summary.json': JSON.stringify(data),
  };
}

function textSummary(data, options) {
  const { indent = '', enableColors = false } = options;
  let summary = '\n';

  // HTTP summary
  if (data.metrics['http_reqs']) {
    const httpData = data.metrics['http_reqs'];
    summary += `${indent}HTTP Summary:\n`;
    summary += `${indent}  Total Requests: ${httpData.value || 0}\n`;
  }

  // Errors summary
  if (data.metrics['http_req_failed']) {
    summary += `${indent}  Failed: ${data.metrics['http_req_failed'].value || 0}\n`;
  }

  // Rate limits
  if (data.metrics['rate_limit_429_count']) {
    summary += `${indent}  Rate Limited (429): ${data.metrics['rate_limit_429_count'].value || 0}\n`;
  }

  // Latency
  if (data.metrics['http_req_duration']) {
    const duration = data.metrics['http_req_duration'];
    summary += `${indent}\nLatency:\n`;
    summary += `${indent}  Avg: ${duration.values?.avg?.toFixed(0)}ms\n`;
    summary += `${indent}  Min: ${duration.values?.min?.toFixed(0)}ms\n`;
    summary += `${indent}  Max: ${duration.values?.max?.toFixed(0)}ms\n`;
    summary += `${indent}  P95: ${duration.values?.['p(95)']?.toFixed(0)}ms\n`;
    summary += `${indent}  P99: ${duration.values?.['p(99)']?.toFixed(0)}ms\n`;
  }

  // Custom metrics
  if (data.metrics['synthesis_latency']) {
    const syn = data.metrics['synthesis_latency'];
    summary += `${indent}\nSynthesis Latency:\n`;
    summary += `${indent}  Avg: ${syn.values?.avg?.toFixed(0)}ms\n`;
    summary += `${indent}  P95: ${syn.values?.['p(95)']?.toFixed(0)}ms\n`;
  }

  if (data.metrics['batch_synthesis_latency']) {
    const batch = data.metrics['batch_synthesis_latency'];
    summary += `${indent}\nBatch Synthesis Latency:\n`;
    summary += `${indent}  Avg: ${batch.values?.avg?.toFixed(0)}ms\n`;
    summary += `${indent}  P95: ${batch.values?.['p(95)']?.toFixed(0)}ms\n`;
  }

  return summary;
}
