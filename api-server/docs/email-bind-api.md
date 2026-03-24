# ClawMeeting 邮箱验证绑定 API 文档

## 概述

邮箱验证绑定采用两步流程：先发送验证码到用户邮箱，用户回复验证码后完成绑定注册。

**Base URL:** `http://39.105.143.2:7010`

---

## API 1: 发送验证码

### `POST /api/auth/send-code`

向指定邮箱发送 6 位数字验证码，验证码有效期 5 分钟，60 秒内不可重复发送。

#### 请求

**Headers:**

| Key | Value |
|-----|-------|
| Content-Type | application/json |

**Body:**

```json
{
  "email": "user@example.com"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 用户邮箱地址（需符合邮箱格式） |

#### 响应

**成功 (200)**

```json
{
  "code": 200,
  "message": "验证码已发送，请查收邮箱",
  "data": null
}
```

**频率限制 (200, code=429)**

```json
{
  "code": 429,
  "message": "请 45 秒后再试",
  "data": null
}
```

**发送失败 (200, code=500)**

```json
{
  "code": 500,
  "message": "邮件发送失败，请稍后重试",
  "data": null
}
```

---

## API 2: 验证码校验 + 绑定注册

### `POST /api/auth/verify-bind`

校验验证码，通过后完成邮箱绑定。如果邮箱对应的用户不存在则自动创建（注册）；如果已存在则标记为已验证并返回已有 token。

#### 请求

**Headers:**

| Key | Value |
|-----|-------|
| Content-Type | application/json |

**Body:**

```json
{
  "email": "user@example.com",
  "code": "123456"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 发送验证码时使用的邮箱 |
| code | string | 是 | 用户收到的 6 位数字验证码 |

#### 响应

**验证通过 - 新用户注册 (200)**

```json
{
  "code": 200,
  "message": "验证并注册成功",
  "data": {
    "token": "sk-xxxxxxxxxxxxxxxx",
    "user_id": 5
  }
}
```

**验证通过 - 已有用户 (200)**

```json
{
  "code": 200,
  "message": "验证成功",
  "data": {
    "token": "sk-xxxxxxxxxxxxxxxx",
    "user_id": 3
  }
}
```

**验证码错误 (200, code=400)**

```json
{
  "code": 400,
  "message": "验证码错误",
  "data": null
}
```

**验证码过期 (200, code=400)**

```json
{
  "code": 400,
  "message": "验证码已过期，请重新获取",
  "data": null
}
```

**验证码不存在 (200, code=400)**

```json
{
  "code": 400,
  "message": "验证码不存在或已过期，请重新获取",
  "data": null
}
```

---

## API 3 (Deprecated): 直接绑定

### `POST /api/auth/bind`

> **已废弃**：此接口无邮箱验证，仅保留向后兼容。新功能请使用 send-code + verify-bind 流程。

---

## 完整流程

```
Step 1: POST /api/auth/send-code
        Body: { "email": "user@example.com" }
        → 用户邮箱收到 6 位验证码

Step 2: POST /api/auth/verify-bind
        Body: { "email": "user@example.com", "code": "123456" }
        → 返回 token + user_id，绑定完成
```

## 业务规则

| 规则 | 说明 |
|------|------|
| 验证码长度 | 6 位纯数字 |
| 验证码有效期 | 5 分钟 |
| 发送间隔限制 | 同一邮箱 60 秒内只能发送一次 |
| 验证码使用次数 | 一次性，验证成功后立即失效 |
| 邮件发送方式 | Loops.so Transactional API |
