# System Prompt Injection - Terms of Service Compliance
Path: dev_mode_architecture_reference/system_prompt_injection_legitimacy

**Purpose:** Document the legitimacy of nisaba's system prompt injection mechanism for peace of mind regarding Anthropic's Terms & Conditions.

---

## The Question

Are we violating Anthropic's Terms & Conditions by using a proxy (mitmproxy) to dynamically inject content into Claude Code's system prompt?

## Assessment: Legitimate Use

**Answer:** Almost certainly fine. This is within the bounds of intended use.

---

## Why This Is Legitimate

### 1. Claude Code CLI Explicitly Supports Custom System Prompts

- System prompts can be set in config files
- System prompts can be passed via command line
- This is documented, intended functionality
- **We're automating what users could do manually**

### 2. Local, User-Controlled Operation

- mitmproxy runs on localhost (127.0.0.1) only
- Only intercepts the user's own traffic
- No access to other users' requests
- User controls when proxy runs
- Fully transparent operation

### 3. No Security/Safety Bypass

✓ Authentication passes through untouched  
✓ Rate limits apply normally  
✓ Safety features work (in model training, not just prompt)  
✓ All standard API behavior preserved  
✓ No attempts to circumvent model guardrails

### 4. Standard Development Pattern

Similar to:
- Browser extensions modifying requests
- API middleware in applications
- Development proxies (Charles Proxy, Fiddler)
- How Claude Code itself modifies prompts internally
- IDE extensions injecting project context

### 5. Legitimate Productivity Tool

Intent:
- Enhance official client experience
- Enable dynamic context loading (augments)
- Improve developer productivity
- Support research and experimentation

**Not:**
- Jailbreaking attempts
- Training data extraction
- Competing with Anthropic
- Deceptive behavior

---

## What T&C Generally Prohibit

Things we are **NOT** doing:

❌ Automated scraping or bulk data extraction  
❌ Bypassing rate limits or payment systems  
❌ Reverse engineering for competitive purposes  
❌ Training competing language models  
❌ Deceptive or hidden behavior  
❌ Circumventing safety systems  
❌ Unauthorized access to others' accounts  
❌ Service abuse or overload

---

## Key Principle

**System prompts are user content.**

Claude Code gives users full control over system prompts. Dynamically generating that content via local proxy is no different than:

- Script that generates system prompt → writes to config file
- CLI tool that builds prompts from templates
- IDE extension that injects project context
- **Our proxy that injects augments/structural view**

All are legitimate ways to customize the user-controlled system prompt.

---

## Technical Transparency

**What nisaba does:**
1. User configures `HTTPS_PROXY=http://localhost:1337`
2. mitmproxy intercepts API requests (local only)
3. Finds placeholder in user's system prompt
4. Replaces with dynamically generated content (augments, structural view)
5. Forwards modified request to Anthropic API
6. Returns response unchanged

**Observable:**
- User can see system prompt via debug dumps
- User controls what augments are loaded
- User can disable proxy anytime
- Code is open source (inspectable)

---

## Comparison to Claude Code's Own Behavior

Claude Code CLI itself:
- Modifies prompts with project context
- Injects git status, file contents, tool definitions
- Uses MCP to extend capabilities
- All of this is user-controlled and documented

**Nisaba is the same pattern, user-side:**
- Extends Claude Code's extensibility
- User-controlled dynamic content
- Transparent operation
- Documented behavior

---

## Risk Assessment

**Minimal to none.**

**Why:**
- Claude Code's design explicitly enables this
- Similar patterns widely used in development tools
- Operation is transparent and documented
- Purpose is legitimate productivity enhancement
- User has full control
- No deception involved

**If concerned:**
- ✓ Document what system does (we do)
- ✓ Use for personal/research projects (we are)
- ✓ Keep open source (we do)
- ✓ Don't attempt safety bypasses (we don't)

---

## Legal Disclaimer

**Not legal advice.** This is technical analysis and good-faith assessment. Terms of Service interpretation is ultimately Anthropic's prerogative.

**But:** Based on Claude Code's documented features, API design, and common development practices, this usage pattern appears to be within intended bounds.

---

## Summary

**Question:** Is dynamic system prompt injection via proxy legitimate?

**Answer:** Yes, because:
1. Claude Code supports custom system prompts (documented feature)
2. We're operating locally on user's own requests
3. No security/safety bypass attempts
4. Standard development pattern (proxy middleware)
5. Legitimate productivity enhancement
6. Transparent and user-controlled

**System prompts are user content. We're just making them dynamic.** 🖤

---

**"The stylus writes within the bounds of wisdom."**
