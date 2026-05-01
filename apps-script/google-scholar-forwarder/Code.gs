// 版权 (c) 2025 [Your Name or Organization], 根据 Apache License 2.0 授权
// 可在 http://www.apache.org/licenses/LICENSE-2.0 获取许可证副本

// --- 全局配置 ---

/**
 * @description Apps Script 属性中保存目标转发邮箱地址的键名。
 * @type {string}
 */
const FORWARDING_ADDRESS_PROPERTY = "FORWARDING_ADDRESS";

/**
 * @description 目标转发邮箱地址 (用于汇总邮件和直接转发)。请在 Apps Script 的
 * Script Properties 中配置 FORWARDING_ADDRESS，避免把真实邮箱提交到仓库。
 * @type {string}
 */
const FORWARDING_ADDRESS = (
  PropertiesService.getScriptProperties().getProperty(FORWARDING_ADDRESS_PROPERTY) || ""
).trim();

/**
 * @description 用于标记【已处理】的 Scholar 关键词快讯的 Gmail 标签名称。
 * @type {string}
 */
const PROCESSED_KEYWORD_LABEL_NAME = "scholar-keywords-processed";

/**
 * @description 用于标记【已处理】的 Scholar 学者相关研究快讯的 Gmail 标签名称。
 * @type {string}
 */
const PROCESSED_AUTHOR_LABEL_NAME = "scholar-author-processed";

/**
 * @description 用于标记【已转发】(无论是直接转发还是汇总转发) 的邮件的 Gmail 标签名称。
 * @type {string}
 */
const FORWARDED_LABEL_NAME = "forwarded-by-script";

/**
 * @description Google Scholar 快讯的发件人地址。
 * @type {string}
 */
const SCHOLAR_SENDER = "scholaralerts-noreply@google.com";

/**
 * @description 用于匹配【关键词】快讯邮件主题的正则表达式 (兼容有无引号)。
 * 匹配 "keyword" - 新的结果 或 keyword - 新的结果
 * @type {RegExp}
 */
const SUBJECT_KEYWORD_REGEX = /^(?:".+"|(?!").+?)\s*-\s*新的结果$/i;

/**
 * @description 用于从【关键词】快讯主题中提取关键词的正则表达式 (兼容有无引号)。
 * @type {RegExp}
 */
const KEYWORD_EXTRACT_REGEX = /^(?:"(.+)"|((?!").+?))\s*-\s*新的结果$/i; // Group 1: quoted, Group 2: unquoted

/**
 * @description 用于匹配【学者相关研究】快讯邮件主题的正则表达式。
 * 匹配 Author Name - 新的相关研究工作
 * @type {RegExp}
 */
const SUBJECT_AUTHOR_REGEX = /^(.*?)\s*-\s*新的相关研究工作$/i;

/**
 * @description 【关键词】汇总邮件的主题。
 * @type {string}
 */
const SUMMARY_KEYWORD_SUBJECT = "每日 Google Scholar 关键词快讯汇总";

/**
 * @description 【学者相关研究】汇总邮件的主题。
 * @type {string}
 */
const SUMMARY_AUTHOR_SUBJECT = "每日 Google Scholar 学者相关研究汇总";

/**
 * @description processIncomingEmails 函数每次运行时最多处理的邮件会话数。
 * @type {number}
 */
const MAX_THREADS_TO_PROCESS = 200;

/**
 * @description 关键词汇总函数每次运行时最多处理的邮件会话总数 (跨多个批次)。
 * @type {number}
 */
const MAX_THREADS_TO_FORWARD_KEYWORD = 100;

/**
 * @description 学者汇总函数每次运行时最多处理的邮件会话总数 (跨多个批次)。
 * @type {number}
 */
const MAX_THREADS_TO_FORWARD_AUTHOR = 100;

/**
 * @description 每个汇总邮件中包含的最大邮件数量，以避免超出邮件大小限制。
 * @type {number}
 */
const SUMMARY_BATCH_SIZE = 7;

/**
 * @description 直接转发邮件时，每次成功转发后暂停的毫秒数。
 * @type {number}
 */
const DIRECT_FORWARD_SLEEP_MS = 100;

// --- 函数 1: 处理收件箱邮件 (应设置较频繁触发, 如每 5 分钟) ---
/**
 * @description 处理收件箱邮件：标记 Scholar 快讯或直接转发其他邮件。
 * 应设置为较频繁运行的时间触发器 (例如，每 5 分钟)。
 */
function processIncomingEmails() {
  Logger.log("开始执行 processIncomingEmails (v5.6.0 - 自定义转发) 脚本..."); // 版本号更新

  // 1. 获取或创建所需标签
  let processedKeywordLabel = getOrCreateLabel_(PROCESSED_KEYWORD_LABEL_NAME);
  let processedAuthorLabel = getOrCreateLabel_(PROCESSED_AUTHOR_LABEL_NAME);
  let forwardedLabel = getOrCreateLabel_(FORWARDED_LABEL_NAME);
  if (!processedKeywordLabel || !processedAuthorLabel || !forwardedLabel) {
    Logger.log(`错误(processIncomingEmails)：无法获取或创建必要的标签。脚本停止。`);
    return;
  }
  Logger.log(`标签 '${PROCESSED_KEYWORD_LABEL_NAME}', '${PROCESSED_AUTHOR_LABEL_NAME}', '${FORWARDED_LABEL_NAME}' 已准备就绪。`);

  // 1.1 验证转发地址
  if (!FORWARDING_ADDRESS || !isValidEmail_(FORWARDING_ADDRESS)) {
    Logger.log(`错误(processIncomingEmails)：${FORWARDING_ADDRESS_PROPERTY} 无效。请在 Script Properties 中设置。脚本停止。`);
    return;
  }

  // 2. 构建通用搜索查询
  const searchQuery = `is:inbox -label:${FORWARDED_LABEL_NAME}`;
  Logger.log(`正在搜索邮件 (processIncomingEmails)，查询: '${searchQuery}'`);

  // 3. 搜索邮件
  let threads;
  try {
    threads = GmailApp.search(searchQuery, 0, MAX_THREADS_TO_PROCESS * 2);
    Logger.log(`初步找到 ${threads.length} 个未最终转发的邮件会话 (processIncomingEmails)。`);
  } catch (e) {
    Logger.log(`搜索邮件时出错 (processIncomingEmails): ${e}`);
    return;
  }

  // 4. 处理邮件会话
  let processedOrForwardedCount = 0;
  let skippedCount = 0;
  const startTime = Date.now();

  for (let i = 0; i < threads.length && processedOrForwardedCount < MAX_THREADS_TO_PROCESS; i++) {

    const thread = threads[i];
    const threadId = thread.getId();

    // 再次检查 forwarded 标签
    if (threadHasLabel_(thread, FORWARDED_LABEL_NAME)) {
      Logger.log(`警告(processIncomingEmails)：会话 ${threadId} 已有 '${FORWARDED_LABEL_NAME}' 标签，跳过。`);
      skippedCount++;
      continue;
    }

    try {
      const messages = thread.getMessages();
      if (messages.length === 0) {
        Logger.log(`警告(processIncomingEmails)：会话 ${threadId} 没有消息，跳过。`);
        skippedCount++;
        continue;
      }
      const latestMessage = messages[messages.length - 1];
      const subject = latestMessage.getSubject();
      const sender = latestMessage.getFrom();

      // --- 判断邮件类型 ---
      const isKeywordAlert = sender.includes(SCHOLAR_SENDER) && SUBJECT_KEYWORD_REGEX.test(subject);
      const isAuthorAlert = sender.includes(SCHOLAR_SENDER) && SUBJECT_AUTHOR_REGEX.test(subject);

      if (isKeywordAlert) {
        // --- 处理 Google Scholar 关键词快讯 ---
        Logger.log(`找到 Scholar 关键词快讯: 会话 ${threadId}, 主题 "${subject}"`);
        if (!threadHasLabel_(thread, PROCESSED_KEYWORD_LABEL_NAME)) {
          Logger.log(`正在为关键词快讯 ${threadId} 添加 '${PROCESSED_KEYWORD_LABEL_NAME}' 标签...`);
          thread.addLabel(processedKeywordLabel);
          processedOrForwardedCount++;
          Utilities.sleep(50);
        } else {
          Logger.log(`关键词快讯 ${threadId} 已有 '${PROCESSED_KEYWORD_LABEL_NAME}' 标签，跳过。`);
          skippedCount++;
        }
      } else if (isAuthorAlert) {
        // --- 处理 Google Scholar 学者相关研究快讯 ---
        Logger.log(`找到 Scholar 学者快讯: 会话 ${threadId}, 主题 "${subject}"`);
        if (!threadHasLabel_(thread, PROCESSED_AUTHOR_LABEL_NAME)) {
          Logger.log(`正在为学者快讯 ${threadId} 添加 '${PROCESSED_AUTHOR_LABEL_NAME}' 标签...`);
          thread.addLabel(processedAuthorLabel);
          processedOrForwardedCount++;
          Utilities.sleep(50);
        } else {
          Logger.log(`学者快讯 ${threadId} 已有 '${PROCESSED_AUTHOR_LABEL_NAME}' 标签，跳过。`);
          skippedCount++;
        }
      } else {
        // --- *** 修改点 开始 (v5.6.0) - 恢复自定义转发 *** ---
        // --- 处理其他邮件：直接转发 ---
        Logger.log(`找到其他邮件: 会话 ${threadId}, 主题 "${subject}". 准备直接转发...`);

        const messageId = latestMessage.getId();
        const originalDate = latestMessage.getDate();
        const originalBody = latestMessage.getBody();
        const originalAttachments = latestMessage.getAttachments();

        // 从 sender 提取邮箱地址
        let originalEmail = sender;
        const emailMatch = sender.match(/<([^>]+)>/);
        if (emailMatch && emailMatch[1]) {
          originalEmail = emailMatch[1];
        }
        Logger.log(`提取到的原始发件人邮箱: ${originalEmail}`);

        // 构造新的主题行，确保只有一个 [Fw: ...] 前缀
        let baseSubject = subject;
        const fwPrefixRegex = /^\[Fw:\s*.*?\]\s*/i;
        if (fwPrefixRegex.test(subject)) {
          let tempSubject = subject;
          while (fwPrefixRegex.test(tempSubject)) {
              tempSubject = tempSubject.replace(fwPrefixRegex, '');
          }
          baseSubject = tempSubject.trim();
          Logger.log(`原始主题包含 Fw: 前缀，提取基础主题: "${baseSubject}"`);
        } else {
            Logger.log(`原始主题不包含 Fw: 前缀。`);
        }
        const newSubject = `[Fw: ${originalEmail}] ${baseSubject}`;
        Logger.log(`构造的新主题: "${newSubject}"`);

        const forwardingInfo = `<p style='color:grey; font-style:italic; border-bottom: 1px solid #ccc; padding-bottom: 10px; margin-bottom: 10px;'>` +
                                `--- 这是一封通过脚本自动转发的邮件 ---<br>` +
                                `<b>原始发件人:</b> ${escapeHtml_(sender)}<br>` +
                                `<b>原始主题:</b> ${escapeHtml_(subject)}<br>` +
                                `<b>原始日期:</b> ${originalDate}<br>` +
                                `<b>邮件会话ID:</b> ${threadId}<br>` +
                                `<b>原始邮件ID:</b> ${messageId}` +
                                `</p>`;
        const newBody = forwardingInfo + originalBody;

        try {
          Logger.log(`正在转发邮件 ${threadId} 至 ${FORWARDING_ADDRESS}...`);
          GmailApp.sendEmail(FORWARDING_ADDRESS, newSubject, "", {
            htmlBody: newBody,
            attachments: originalAttachments,
            name: 'Gmail 自动转发机器人'
          });
          Logger.log(`邮件 ${threadId} 转发成功。`);

          Logger.log(`正在为邮件 ${threadId} 添加 '${FORWARDED_LABEL_NAME}' 标签...`);
          thread.addLabel(forwardedLabel);
          Logger.log(`邮件 ${threadId} 标记为已转发。`);
          processedOrForwardedCount++;

          Logger.log(`暂停 ${DIRECT_FORWARD_SLEEP_MS} 毫秒...`);
          Utilities.sleep(DIRECT_FORWARD_SLEEP_MS);

        } catch (sendOrLabelError) {
            Logger.log(`转发或标记邮件 ${threadId} 时出错: ${sendOrLabelError}`);
            if (isQuotaError_(sendOrLabelError)) {
                Logger.log("检测到配额用尽 (processIncomingEmails - forwarding)。停止处理剩余邮件。");
                break; // 跳出循环
            }
            // 如果是邮件大小错误，则重新抛出以触发通知
            if (sendOrLabelError.toString().includes("Email Body Size")) {
                Logger.log(`错误(processIncomingEmails): 转发邮件 ${threadId} 时超出邮件大小限制。`);
                throw sendOrLabelError; // 重新抛出错误
            }
            // 对于其他错误，记录并继续处理下一个邮件
        }
        // --- *** 修改点 结束 (v5.6.0) *** ---
      } // end if/else if/else

    } catch (e) {
      Logger.log(`处理会话 ${threadId} 时发生意外错误 (processIncomingEmails): ${e}`);
      if (isQuotaError_(e)) {
        Logger.log("检测到配额用尽 (processIncomingEmails - outer loop)。停止处理剩余邮件。");
        break; // 跳出循环
      }
      // 对于其他错误，记录并继续处理下一个会话
    }
  } // end for loop

  const endTime = Date.now();
  Logger.log("本次运行总结 (processIncomingEmails v5.6.0):"); // 版本号更新
  Logger.log(`  - 初步找到会话数: ${threads ? threads.length : 0}`);
  Logger.log(`  - 成功标记(关键词/学者)或转发(其他)数: ${processedOrForwardedCount}`);
  Logger.log(`  - 跳过会话数: ${skippedCount}`);
  Logger.log(`  - 总耗时: ${((endTime - startTime) / 1000).toFixed(2)} 秒`);
  Logger.log("processIncomingEmails (v5.6.0) 脚本执行完毕。");
}


// --- 函数 2: 汇总并转发【关键词】快讯 ---

/**
 * @description [早上 7 点运行] 汇总【关键词】快讯。
 */
function summarizeKeywordAlerts_Morning() {
  const functionName = "summarizeKeywordAlerts_Morning";
  const alertType = "关键词";
  const runTime = "早上";
  const processedLabelName = PROCESSED_KEYWORD_LABEL_NAME;
  const summarySubject = SUMMARY_KEYWORD_SUBJECT;
  const maxThreadsToForward = MAX_THREADS_TO_FORWARD_KEYWORD;
  const headingColor = "#1a73e8"; // Blue for keywords
  const borderColor = "#4285f4";

  summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, KEYWORD_EXTRACT_REGEX, 1);
}

/**
 * @description [中午 12 点运行] 汇总【关键词】快讯。
 */
function summarizeKeywordAlerts_Noon() {
  const functionName = "summarizeKeywordAlerts_Noon";
  const alertType = "关键词";
  const runTime = "中午";
  const processedLabelName = PROCESSED_KEYWORD_LABEL_NAME;
  const summarySubject = SUMMARY_KEYWORD_SUBJECT;
  const maxThreadsToForward = MAX_THREADS_TO_FORWARD_KEYWORD;
  const headingColor = "#1a73e8"; // Blue for keywords
  const borderColor = "#4285f4";

  summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, KEYWORD_EXTRACT_REGEX, 1);
}

/**
 * @description [晚上 7 点运行] 汇总【关键词】快讯。
 */
function summarizeKeywordAlerts_Evening() {
  const functionName = "summarizeKeywordAlerts_Evening";
  const alertType = "关键词";
  const runTime = "晚上";
  const processedLabelName = PROCESSED_KEYWORD_LABEL_NAME;
  const summarySubject = SUMMARY_KEYWORD_SUBJECT;
  const maxThreadsToForward = MAX_THREADS_TO_FORWARD_KEYWORD;
  const headingColor = "#1a73e8"; // Blue for keywords
  const borderColor = "#4285f4";

  summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, KEYWORD_EXTRACT_REGEX, 1);
}


// --- 函数 3: 汇总并转发【学者相关研究】快讯 ---

/**
 * @description [早上 7 点运行] 汇总【学者相关研究】快讯。
 */
function summarizeAuthorAlerts_Morning() {
  const functionName = "summarizeAuthorAlerts_Morning";
  const alertType = "学者";
  const runTime = "早上";
  const processedLabelName = PROCESSED_AUTHOR_LABEL_NAME;
  const summarySubject = SUMMARY_AUTHOR_SUBJECT;
  const maxThreadsToForward = MAX_THREADS_TO_FORWARD_AUTHOR;
  const headingColor = "#1e8e3e"; // Green for authors
  const borderColor = "#34a853";

  summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, SUBJECT_AUTHOR_REGEX, 1);
}

/**
 * @description [中午 12 点运行] 汇总【学者相关研究】快讯。
 */
function summarizeAuthorAlerts_Noon() {
  const functionName = "summarizeAuthorAlerts_Noon";
  const alertType = "学者";
  const runTime = "中午";
  const processedLabelName = PROCESSED_AUTHOR_LABEL_NAME;
  const summarySubject = SUMMARY_AUTHOR_SUBJECT;
  const maxThreadsToForward = MAX_THREADS_TO_FORWARD_AUTHOR;
  const headingColor = "#1e8e3e"; // Green for authors
  const borderColor = "#34a853";

  summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, SUBJECT_AUTHOR_REGEX, 1);
}

/**
 * @description [晚上 7 点运行] 汇总【学者相关研究】快讯。
 */
function summarizeAuthorAlerts_Evening() {
  const functionName = "summarizeAuthorAlerts_Evening";
  const alertType = "学者";
  const runTime = "晚上";
  const processedLabelName = PROCESSED_AUTHOR_LABEL_NAME;
  const summarySubject = SUMMARY_AUTHOR_SUBJECT;
  const maxThreadsToForward = MAX_THREADS_TO_FORWARD_AUTHOR;
  const headingColor = "#1e8e3e"; // Green for authors
  const borderColor = "#34a853";

  summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, SUBJECT_AUTHOR_REGEX, 1);
}


// --- 通用汇总与批处理函数 ---
/**
 * @description 通用的汇总函数，处理指定类型的快讯，分批发送。
 * @param {string} functionName 调用此函数的函数名称 (用于日志)。
 * @param {string} alertType 快讯类型 ("关键词" 或 "学者") (用于日志和标题)。
 * @param {string} runTime 运行时间 ("早上", "中午" 或 "晚上") (用于日志和标题)。
 * @param {string} processedLabelName 用于标记已处理邮件的标签名。
 * @param {string} summarySubject 汇总邮件的基础主题。
 * @param {number} maxThreadsToForward 本次运行最多处理的邮件总数。
 * @param {string} headingColor HTML 标题颜色。
 * @param {string} borderColor HTML 边框颜色。
 * @param {RegExp} subjectRegex 用于匹配和提取信息的邮件主题正则表达式。
 * @param {number} infoGroupIndex 正则表达式中包含所需信息 (关键词/学者名) 的捕获组索引 (从 1 开始)。
 * @private
 */
function summarizeAlertsInBatches_(functionName, alertType, runTime, processedLabelName, summarySubject, maxThreadsToForward, headingColor, borderColor, subjectRegex, infoGroupIndex) {
  Logger.log(`开始执行 ${functionName} (v5.6.0 - ${alertType}汇总-${runTime}) 脚本...`); // 版本号更新
  const startTime = Date.now();

  // 1. 验证配置
  if (!FORWARDING_ADDRESS || !isValidEmail_(FORWARDING_ADDRESS)) {
    Logger.log(`错误(${functionName})：${FORWARDING_ADDRESS_PROPERTY} 无效。请在 Script Properties 中设置。脚本停止。`);
    return;
  }

  // 2. 获取或创建标签
  let processedLabel = GmailApp.getUserLabelByName(processedLabelName);
  if (!processedLabel) {
    Logger.log(`错误(${functionName})：处理标签 '${processedLabelName}' 不存在。脚本停止。`);
    return;
  }
  let forwardedLabel = getOrCreateLabel_(FORWARDED_LABEL_NAME);
  if (!forwardedLabel) {
    Logger.log(`错误(${functionName})：无法获取或创建转发标签 '${FORWARDED_LABEL_NAME}'。脚本停止。`);
    return;
  }
  Logger.log(`标签 '${processedLabelName}' 和 '${FORWARDED_LABEL_NAME}' 已准备就绪 (${alertType}汇总-${runTime})。`);

  let totalProcessedInThisRun = 0;
  let totalBatchesSent = 0;

  // --- 批处理循环 ---
  while (true) {

    // 检查是否已达到本次运行的处理上限
    if (totalProcessedInThisRun >= maxThreadsToForward) {
      Logger.log(`已达到本次运行处理上限 (${maxThreadsToForward} 个)，停止处理更多批次 (${functionName})。`);
      break;
    }

    // 3. 查找下一批待处理邮件
    const searchQuery = `label:${processedLabelName} -label:${FORWARDED_LABEL_NAME}`;
    const batchLimit = Math.min(SUMMARY_BATCH_SIZE, maxThreadsToForward - totalProcessedInThisRun);
    Logger.log(`正在搜索下一批待汇总【${alertType}】邮件(${runTime})，查询: '${searchQuery}', 批次大小上限: ${batchLimit}`);

    let threadsInBatch;
    try {
      threadsInBatch = GmailApp.search(searchQuery, 0, batchLimit);
      Logger.log(`找到 ${threadsInBatch.length} 个【${alertType}】邮件会话用于当前批次(${runTime})。`);

      if (threadsInBatch.length === 0) {
        Logger.log(`没有更多需要汇总转发的【${alertType}】邮件(${runTime})。`);
        break; // 退出批处理循环
      }
    } catch (e) {
      Logger.log(`搜索下一批【${alertType}】邮件时出错(${runTime}): ${e}`);
      break; // 搜索失败，停止处理
    }

    // 4. 汇总本批次邮件内容
    const currentBatchNumber = totalBatchesSent + 1;
    const batchSubject = `${summarySubject} (${runTime}批次 ${currentBatchNumber})`;
    let summaryBodyHtml = `<h1 style="color: ${headingColor};">${escapeHtml_(batchSubject)}</h1>`;
    summaryBodyHtml += `<p>以下是本批次收集到的 Google Scholar ${alertType}快讯：</p><hr style="border: none; border-top: 1px solid #ccc; margin: 15px 0;">`;
    let collectedInBatch = 0;
    const threadsSuccessfullyProcessedInBatch = [];

    for (let i = 0; i < threadsInBatch.length; i++) {
      const thread = threadsInBatch[i];
      const threadId = thread.getId();
      try {
        const messages = thread.getMessages();
        if (messages.length === 0) continue;
        const latestMessage = messages[messages.length - 1];
        const subject = latestMessage.getSubject();
        const sender = latestMessage.getFrom();
        const match = subject.match(subjectRegex);

        // 严格检查邮件格式
        if (!(sender.includes(SCHOLAR_SENDER) && match)) {
          Logger.log(`警告(${functionName})：会话 ${threadId} (主题: "${subject}") 不符${alertType}快讯格式，跳过汇总。`);
          continue;
        }

        const body = latestMessage.getBody();
        const messageDate = latestMessage.getDate();
        // 提取信息 (关键词或学者名)
        let info = "未知信息";
        if (subjectRegex === KEYWORD_EXTRACT_REGEX) {
            info = match[1] || match[2] || `未知${alertType}`; // Group 1 or 2 for keywords
        } else if (subjectRegex === SUBJECT_AUTHOR_REGEX && match[1]) { // 确保 match[1] 存在
            info = match[1]; // Group 1 for author
        } else {
            info = `未知${alertType}`;
        }

        summaryBodyHtml += `<div style="margin-bottom: 25px; padding: 15px; border-left: 4px solid ${borderColor}; background-color: #f8f9fa; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">`;
        summaryBodyHtml += `<h2 style="margin-top: 0; margin-bottom: 5px; color: ${headingColor}; font-size: 1.2em;">${alertType}: ${escapeHtml_(info)}</h2>`;
        summaryBodyHtml += `<p style="font-size: 0.85em; color: #5f6368; margin-bottom: 10px;">原始邮件日期: ${messageDate} | 会话ID: ${threadId}</p>`;
        summaryBodyHtml += `<div style="border-top: 1px dashed #ddd; padding-top: 10px;">${body}</div>`; // 显示原始邮件内容
        summaryBodyHtml += `</div>`;

        collectedInBatch++;
        threadsSuccessfullyProcessedInBatch.push(thread); // 记录成功处理的会话，用于后续标记

      } catch (e) {
        Logger.log(`汇总处理【${alertType}】会话 ${threadId} 时出错(${runTime}): ${e}`);
        // 即使单个邮件处理出错，也继续处理本批次下一个
      }
    } // end loop for threads in batch

    // 5. 发送本批次的汇总邮件
    if (collectedInBatch > 0) {
      Logger.log(`本批次汇总了 ${collectedInBatch} 条【${alertType}】快讯(${runTime})。准备发送至 ${FORWARDING_ADDRESS}...`);
      try {
        GmailApp.sendEmail(FORWARDING_ADDRESS, batchSubject, "请在支持 HTML 的客户端中查看此邮件。", {
          htmlBody: summaryBodyHtml,
          name: 'Gmail 自动转发机器人'
        });
        Logger.log(`【${alertType}】汇总邮件批次 ${currentBatchNumber} 发送成功(${runTime})。`);
        totalBatchesSent++;

        // 6. 为本批次已成功汇总的邮件添加最终转发标签
        Logger.log(`准备为本批次 ${threadsSuccessfullyProcessedInBatch.length} 个已汇总【${alertType}】会话添加 '${FORWARDED_LABEL_NAME}' 标签(${runTime})...`);
        let taggedInBatch = 0;
        for (const thread of threadsSuccessfullyProcessedInBatch) {
          try {
            if (!threadHasLabel_(thread, FORWARDED_LABEL_NAME)) {
              thread.addLabel(forwardedLabel);
              taggedInBatch++;
              Utilities.sleep(50); // 短暂暂停
            } else {
              Logger.log(`警告(${functionName}-批次${currentBatchNumber})：会话 ${thread.getId()} 在汇总标记前已存在 '${FORWARDED_LABEL_NAME}' 标签。`);
            }
          } catch (e) {
            Logger.log(`为汇总【${alertType}】会话 ${thread.getId()} 添加 '${FORWARDED_LABEL_NAME}' 标签时出错(${runTime}-批次${currentBatchNumber}): ${e}`);
            if (isQuotaError_(e)) {
              Logger.log(`检测到配额用尽 (${functionName} - tagging)。停止标记本批次剩余邮件。`);
              break; // 停止标记本批次，但如果时间允许，外部循环会尝试下一批
            }
          }
        }
        Logger.log(`成功为 ${taggedInBatch} 个汇总【${alertType}】会话添加了 '${FORWARDED_LABEL_NAME}' 标签(${runTime}-批次${currentBatchNumber})。`);
        totalProcessedInThisRun += taggedInBatch; // 累加本次运行处理的总数

      } catch (e) {
        Logger.log(`发送【${alertType}】汇总邮件批次 ${currentBatchNumber} 时出错(${runTime}): ${e}`);
        // 检查是否是邮件大小错误
        if (e.toString().includes("Email Body Size")) {
            Logger.log(`错误(${functionName}): 发送批次 ${currentBatchNumber} 时超出邮件大小限制 (批次大小: ${SUMMARY_BATCH_SIZE})。请考虑减小 SUMMARY_BATCH_SIZE 的值。脚本将停止。`);
            // 重新抛出错误以触发 Apps Script 的通知机制
            throw e;
        } else {
            // 对于其他发送错误，记录日志并停止处理更多批次，但不抛出错误
            Logger.log(`由于发送失败 (非大小限制)，本批次未标记【${alertType}】汇总邮件为已转发(${runTime}-批次${currentBatchNumber})。停止处理更多批次。`);
            break; // 退出批处理循环
        }
      }
    } else {
      Logger.log(`本批次没有收集到任何有效的【${alertType}】快讯内容进行汇总(${runTime})。`);
    }
  } // --- End of while loop for batches ---

  const endTime = Date.now();
  Logger.log(`本次运行总结 (${functionName} v5.6.0 - ${alertType}汇总-${runTime}):`); // 版本号更新
  Logger.log(`  - 发送批次数: ${totalBatchesSent}`);
  Logger.log(`  - 成功标记邮件总数: ${totalProcessedInThisRun}`);
  Logger.log(`  - 总耗时: ${((endTime - startTime) / 1000).toFixed(2)} 秒`);
  Logger.log(`${functionName} (v5.6.0 - ${alertType}汇总-${runTime}) 脚本执行完毕。`);
}


// --- 辅助函数 (共享) ---

/**
 * @description 获取或创建指定的 Gmail 标签。
 * @param {string} labelName 要获取或创建的标签名称。
 * @returns {GmailApp.GmailLabel|null} 返回 GmailLabel 对象，如果创建失败则返回 null。
 * @private
 */
function getOrCreateLabel_(labelName) {
  let label = GmailApp.getUserLabelByName(labelName);
  if (!label) {
    Logger.log(`标签 '${labelName}' 不存在，尝试创建...`);
    try {
      label = GmailApp.createLabel(labelName);
      Logger.log(`标签 '${labelName}' 已成功创建。`);
    } catch (e) {
      Logger.log(`错误：无法创建标签 '${labelName}'。错误: ${e}`);
      return null;
    }
  }
  return label;
}

/**
 * @description 检查邮件会话是否已包含特定标签。
 * @param {GmailApp.GmailThread} thread 要检查的邮件会话。
 * @param {string} labelName 要检查的标签名称。
 * @returns {boolean} 如果会话包含该标签，则返回 true，否则返回 false。
 * @private
 */
function threadHasLabel_(thread, labelName) {
  const labels = thread.getLabels();
  for (let i = 0; i < labels.length; i++) {
    if (labels[i].getName() === labelName) {
      return true;
    }
  }
  return false;
}

/**
 * @description 简单的 HTML 转义函数。
 * @param {*} text 要转义的文本。
 * @returns {string} 转义后的 HTML 文本或原始值。
 * @private
 */
function escapeHtml_(text) {
  if (typeof text !== 'string') {
    return text; // 如果不是字符串，直接返回
  }
  return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
}

/**
 * @description 简单的邮箱地址格式验证。
 * @param {string} email 要验证的邮箱地址。
 * @returns {boolean} 如果格式基本正确，返回 true，否则返回 false。
 * @private
 */
function isValidEmail_(email) {
    if (!email || typeof email !== 'string') {
        return false;
    }
    // 使用更标准的正则表达式进行验证
    const emailRegex = /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/;
    return emailRegex.test(email);
}

/**
 * @description 检查错误是否与配额相关 (不包括邮件大小限制)。
 * @param {Error} error 捕获到的错误对象。
 * @returns {boolean} 如果错误消息包含配额相关的关键字 (非大小限制)，返回 true。
 * @private
 */
function isQuotaError_(error) {
    if (!error || !error.toString) return false;
    const errorString = error.toString().toLowerCase();
    // 检查常见的配额错误，但排除邮件大小错误
    return (errorString.includes("limit exceeded") || errorString.includes("service invoked too many times"))
            && !errorString.includes("email body size");
}
