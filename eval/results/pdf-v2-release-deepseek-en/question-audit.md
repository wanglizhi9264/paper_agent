# 60 题逐题审计

> 数据来自 `eval\results\pdf-v2-release-deepseek-en\predictions.json` 的 Full Pipeline 真实运行。答案判定采用结构化答案点字面覆盖，
> 不等同于人工或 LLM 语义裁判；检索与引用判定使用冻结 chunk labels。

## 汇总

- 题目数：60
- 答案判定：{"答案点全部命中": 12, "错判：可回答题被拒答": 13, "疑似错答：答案点均未命中": 4, "部分正确：仅命中部分答案点": 23, "正确拒答": 8}
- 检索判定：{"Top-10 未命中": 24, "Top-10 全命中": 16, "Top-10 部分命中": 12, "不适用（不可回答）": 8}
- 引用判定：{"错引：引用未命中标注证据": 17, "漏引：没有引用": 13, "所需引用全部命中": 12, "部分引用命中": 10, "无引用": 8}

## 逐题结果

### 1. eval-001

- 问题：What Inception Score and FID does DDPM report on unconditional CIFAR-10?
- 标准答案：Inception Score 为 9.46（表中为 9.46±0.11），FID 为 3.17。
- 标准答案点：9.46、3.17
- 模型答案：DDPM reports an Inception score of 9.46 and an FID score of 3.17 on unconditional CIFAR-10 [1][4].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"67e6fa9a-205e-4b30-9ffc-fbaf40fb8155": null}`
- 命中答案点：9.46、3.17
- 答案判断：答案点全部命中
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：11606.661 ms

### 2. eval-002

- 问题：What classification accuracy does EEG2IM achieve on ImageNet-4, and what are its IS and FID for image generation?
- 标准答案：分类准确率为 92.55%，生成 IS 为 8.79，FID 为 19.49。
- 标准答案点：92.55%、8.79、19.49
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"a3cc4804-4b8b-4620-b933-285d935eab62": null, "3264452c-a2b9-47ad-b539-ed2174929164": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：8962.423 ms

### 3. eval-003

- 问题：How large is MotionVerse in LMM in terms of tasks, datasets, sequences, and frames?
- 标准答案：MotionVerse contains 10 tasks, 16 datasets, about 320k sequences, and 100 million frames.
- 标准答案点：10 tasks、16 datasets、320k sequences、100 million frames
- 模型答案：MotionVerse comprises **10 tasks**, **16 datasets**, **320k sequences**, and **100 million frames** [2][4].
- 正确证据首次排名：2
- 各标注证据排名：`{"0f8f8d42-d30d-4d95-99cb-d0a43f8f71bf": 2}`
- 命中答案点：10 tasks、16 datasets、320k sequences、100 million frames
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：8626.499 ms

### 4. eval-004

- 问题：On which three action datasets does the ACTOR paper conduct its main evaluations?
- 标准答案：NTU RGB+D（论文实验中使用 NTU-13 子集）、HumanAct12 和 UESTC。
- 标准答案点：NTU RGB+D、HumanAct12、UESTC
- 模型答案：The ACTOR paper evaluates its approach on the NTU RGB+D, HumanAct12, and UESTC datasets [2][3][8].
- 正确证据首次排名：8
- 各标注证据排名：`{"2956f056-2d15-46f0-aa47-45a204711288": 8}`
- 命中答案点：NTU RGB+D、HumanAct12、UESTC
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：8706.601 ms

### 5. eval-005

- 问题：According to the paper, which two data sources are used by the human motion intention prediction method to construct the signal branch and the sensor branch, respectively?
- 标准答案：论文写道：EEG/EMG 信号来自 HumanAct12，传感器数据来自 MotionID: IMU-specific motion dataset。
- 标准答案点：HumanAct12、MotionID: IMU-specific motion dataset
- 模型答案：According to the paper, the signal branch uses **EEG/EMG signals**, and the sensor branch uses **sensor measure data** [2]. Specifically, the EEG/EMG signals are collected from the **HumanAct1239 dataset**, while the sensor measure data are obtained from the **MotionID: IMU-specific motion dataset** [6].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"11b56078-0d86-474d-8234-0255d084c24b": null, "30c1a577-4251-4659-b0b7-9c94e53ffd1e": null}`
- 命中答案点：HumanAct12、MotionID: IMU-specific motion dataset
- 答案判断：答案点全部命中
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：10714.129 ms

### 6. eval-006

- 问题：What does Science Research Writing recommend regarding the typical length and paragraph format of an abstract?
- 标准答案：多数摘要为 80–150 词并写成单段；即使较长的 150–250 词摘要通常也仍是单段。
- 标准答案点：80–150 words、single paragraph、150–250 words
- 模型答案：According to the source, an abstract usually has a strict word limit, with most being between 80–150 words and written as a single paragraph. Even longer abstracts (150–250 words) are usually written as a single paragraph, and one should not submit an abstract over the word limit [1].
- 正确证据首次排名：1
- 各标注证据排名：`{"2d6e5447-474a-40cd-b98e-63a925dd9f63": 1}`
- 命中答案点：80–150 words、single paragraph、150–250 words
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：9858.954 ms

### 7. eval-007

- 问题：How many participants and image classes does the ImageNet-4 EEG dataset contain, and how many unique images are there per class?
- 标准答案：26 名参与者，4 类（cat、dog、flower、panda），每类 50 张独特图像，共 200 张。
- 标准答案点：26 participants、4 classes、50 unique images per class、200 images
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"497b64d8-c207-4290-9fd7-4bc83390c3a1": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：9733.652 ms

### 8. eval-008

- 问题：How many parameters do the LMM-Tiny, Small, Base, and Large models have, respectively?
- 标准答案：分别约为 90M、160M、410M、760M 参数。
- 标准答案点：90M、160M、410M、760M
- 模型答案：LMM-Tiny has 90M parameters, LMM-Small has 160M, LMM-Base has 410M, and LMM-Large has 760M parameters [1][4].
- 正确证据首次排名：1
- 各标注证据排名：`{"650f1356-ac36-42fd-ad21-b9cffe886b04": 1}`
- 命中答案点：90M、160M、410M、760M
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：8629.03 ms

### 9. eval-009

- 问题：How many action classes are in UESTC, and how many training and test sequences are there under the official cross-subject split used in the ACTOR experiments?
- 标准答案：UESTC 有 40 个动作类别；使用官方 cross-subject 协议后有 10,650 条训练序列和 13,350 条测试序列。
- 标准答案点：40 action categories、10650、13350
- 模型答案：The UESTC dataset has 40 action categories [1]. Under the official cross-subject split used in the ACTOR experiments, there are 10,650 training sequences and 13,350 test sequences [2].
- 正确证据首次排名：1
- 各标注证据排名：`{"dc95dfa8-999e-4a89-948b-76f11b303ced": 1, "5330396d-b32b-449d-b837-379cc2b56f26": 2}`
- 命中答案点：40 action categories、10650、13350
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：8922.582 ms

### 10. eval-010

- 问题：According to the writing guide, when is the best time to write the Introduction?
- 标准答案：After the report sections have been written or at least drafted, because the writer needs to be clear about what was done and found.
- 标准答案点：after、written、drafted、report sections
- 模型答案：According to the writing guide, the best time to write the Introduction is after you have written, or at least drafted, the report sections [1].
- 正确证据首次排名：1
- 各标注证据排名：`{"8a8d8908-b526-47fa-80b3-29eddcad9e50": 1}`
- 命中答案点：after、written、drafted、report sections
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：8989.599 ms

### 11. eval-011

- 问题：What do the forward and reverse processes of DDPM do, respectively?
- 标准答案：前向过程按马尔可夫链逐步向数据加入高斯噪声直到信号被破坏；反向过程学习条件高斯转移，从噪声逐步恢复数据。
- 标准答案点：forward adds noise、reverse learns to denoise
- 模型答案：The forward process (diffusion process) is a fixed Markov chain that gradually adds Gaussian noise to the data according to a variance schedule β₁, …, β_T [2]. The reverse process is a learned Markov chain with Gaussian transitions that starts from a standard normal prior p(x_T) = N(0, I) and gradually recovers the data by denoising [6].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"1a975488-3e74-4215-bdf1-4c1f15a52999": null}`
- 命中答案点：无
- 答案判断：疑似错答：答案点均未命中
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：10682.969 ms

### 12. eval-012

- 问题：Why can DDPM parameterize the reverse process by predicting noise epsilon, and with which classical method does this parameterization establish a connection?
- 标准答案：论文将均值参数化为由 εθ(xt,t) 预测加入的噪声 ε，从而把训练目标化为多噪声尺度的去噪误差；这与 denoising score matching 以及 Langevin dynamics 建立联系。
- 标准答案点：predict epsilon、denoising score matching、Langevin dynamics
- 模型答案：DDPM can parameterize the reverse process by predicting the noise \(\epsilon\) because the reverse-process mean \(\mu_\theta\) has the form  
\[
\frac{1}{\sqrt{\alpha_t}}\left(x_t - \frac{\beta_t}{\sqrt{1-\bar\alpha_t}}\epsilon\right),
\]
so, since \(x_t\) is known as model input, predicting \(\epsilon\) is an equivalent reparameterization of predicting the mean [7].

This \(\epsilon\)-prediction parameterization establishes a connection between diffusion models and **denoising score matching over multiple noise levels with annealed Langevin dynamics for sampling** [1]. It also resembles Langevin dynamics and simplifies the diffusion model's variational bound to an objective resembling denoising score matching [4].
- 正确证据首次排名：7
- 各标注证据排名：`{"8120dfb4-f32a-4391-acba-c909e85e6ed8": null, "05a0e75b-f385-4245-b15e-bb090eccf12c": null, "5c31b2a5-e192-455d-9292-a830b6061049": 7, "a1c50c18-93db-4ba7-bfb1-0593dfa9e8e6": null, "4ea04bce-fdd4-403a-b3b0-477307a4ec50": null, "f73994dc-882f-4299-83b0-49e697c8e519": null}`
- 命中答案点：denoising score matching、Langevin dynamics
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：12459.444 ms

### 13. eval-013

- 问题：How are high-level semantic features learned from EEG in EEG2IM?
- 标准答案：高层 EEG 编码器作为学生模型，从冻结的 ResNet50 教师学习，同时使用 response-based 与 feature-based knowledge distillation；分别用输出分布和中间特征进行语义对齐。
- 标准答案点：student、frozen ResNet50、response-based、feature-based
- 模型答案：In EEG2IM, high-level semantic features are learned using a high-level semantic encoder that is trained via knowledge distillation, where it learns from a ResNet50 teacher model through both response-based and feature-based methods [2].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"40d94a3a-0892-4bf0-aa17-27c25e9ce3eb": null, "8c8f1f90-529a-4b57-a181-0073f9f4dc57": null, "39c908e3-bd74-47a3-bb5b-604e220ffe94": null}`
- 命中答案点：response-based、feature-based
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：8077.038 ms

### 14. eval-014

- 问题：How does EEG2IM extract low-level semantics and inject both high- and low-level conditions into the diffusion model?
- 标准答案：低层 EEG 编码器通过与图像 autoencoder 联合训练，将 EEG 特征对齐到包含颜色、轮廓等细节的图像潜在特征；随后用 FiLM 根据高低层条件产生缩放 γ 和偏移 β，在 U-Net 各层调制特征图。
- 标准答案点：joint training、autoencoder、FiLM、gamma、beta
- 模型答案：EEG2IM extracts low-level semantics using a **low-level semantic encoder**, which captures fine-grained attributes and is aligned with image features from an autoencoder via joint training [2]. Both the high-level features (from the high-level semantic encoder) and the low-level features are then incorporated into the diffusion model using **Feature-wise Linear Modulation (FiLM)**, enabling precise control over image synthesis while preserving semantic consistency and fine details [3].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"1e52251d-c6d9-4046-a26c-fe1b1f85d66c": null, "d6e8c15e-b049-409c-bc8b-1799b6e1a86b": null}`
- 命中答案点：joint training、autoencoder、FiLM
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：9885.857 ms

### 15. eval-015

- 问题：How does LMM unify motions from different datasets and different body-representation formats into a common training space?
- 标准答案：LMM 先把各数据集对齐到统一的 TOMATO 中间表示，再把运动划分为 10 个身体部分并标记缺失部分；测试时用 representation translators 转回各数据集要求的表示。
- 标准答案点：TOMATO、10 parts、representation translators
- 模型答案：LMM unifies motions from different datasets and body-representation formats by converting the motion formats of various datasets into a unified intermediate representation, enabling the model to acquire common knowledge from originally diverse data formats [8]. All motion data are aligned to this unified intermediate format, and all condition signals are converted into token sequences that are closer in feature space [3]. This alignment is part of the broader MotionVerse benchmark, which consolidates datasets with different modalities, formats, and tasks into a comprehensive, unified motion generation dataset [2]. Additionally, body part-aware modeling decomposes motion data from different datasets into relatively independent body segments, allowing the model to leverage knowledge learned across datasets [4].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"142b9edd-52e8-461e-bc21-3fb4d1cbd4b0": null}`
- 命中答案点：无
- 答案判断：疑似错答：答案点均未命中
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：9784.208 ms

### 16. eval-016

- 问题：What are the two main attention components in LMM's ArtAttention, and what does each model?
- 标准答案：ArtAttention contains body-part (spatial) attention and temporal attention. Spatial attention models interactions among body parts while respecting missing/masked parts; temporal attention models temporal self-correlation and condition guidance for each body part.
- 标准答案点：body-part attention、spatial attention、temporal attention、temporal correlations
- 模型答案：The two main attention components in LMM’s ArtAttention are **body-part attention (spatial attention)** and **temporal attention** [1][5].

- **Body-part (spatial) attention** models the mutual contributions among body parts on a per-frame basis; because body parts can be inherently missing or artificially masked, it uses an attention structure to produce refined frame-level features rather than fixed coefficients [1].
- **Temporal attention** is the other branch of ArtAttention, with the mechanism designed for spatial-temporal independence [2][5]. The sources describe it as the temporal attention component, but do not provide further detail on its exact modeling beyond its role in the temporal attention branch [1][5].
- 正确证据首次排名：1
- 各标注证据排名：`{"b58ade50-546c-4b8c-8d76-d1c73da4a4ba": 1, "9c064c5b-1a98-46bf-b3f0-3e0d21351515": null}`
- 命中答案点：body-part attention、spatial attention、temporal attention
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：12715.199 ms

### 17. eval-017

- 问题：How does ACTOR's encoder compress a motion sequence of arbitrary length into a sequence-level latent vector?
- 标准答案：它在姿态序列前加入每个动作对应的两个可学习 distribution-parameter tokens（μ token 和 Σ token），经过 Transformer 后取这两个 token 的输出得到 μ、Σ，再通过重参数化采样单个 latent z。
- 标准答案点：distribution parameter tokens、mu、Sigma、sequence-level、latent z
- 模型答案：ACTOR’s encoder compresses an arbitrary-length motion sequence into a sequence-level latent vector by using learnable “distribution parameter tokens” to pool the temporal dimension [1][2]. Specifically, the pose parameters and translations are linearly embedded, and two learnable tokens per action—\(\mu_{\text{token}}^a\) and \(\Sigma_{\text{token}}^a\)—are prepended to the embedded pose sequence [2]. The Transformer encoder processes this input, and the first two encoder outputs corresponding to the distribution parameter tokens are taken as the pooled distribution parameters \(\mu\) and \(\Sigma\), while the rest are discarded [2]. A latent vector \(z\) is then sampled from this distribution using the reparameterization trick [1].
- 正确证据首次排名：2
- 各标注证据排名：`{"9f61302e-ab08-4fba-b1c3-ba9f842ac10b": 2}`
- 命中答案点：distribution parameter tokens、mu、Sigma、sequence-level
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：11201.41 ms

### 18. eval-018

- 问题：Why is ACTOR's decoder called non-autoregressive, and how does it control the length of the generated sequence?
- 标准答案：decoder 一次性生成完整运动序列，不逐帧依赖前一帧；它把目标时长对应的 T 个 sinusoidal positional encodings 作为 query，并以 latent z 加动作信息作为 key/value。
- 标准答案点：one shot、not autoregressive、T sinusoidal positional encodings
- 模型答案：ACTOR's decoder is called non-autoregressive because it generates the full motion sequence in one shot, rather than predicting frame-by-frame autoregressively [2][5]. Length is controlled by specifying a sequence of positional encodings to the decoder, which determines the duration of the synthesized motion [1][5][7].
- 正确证据首次排名：2
- 各标注证据排名：`{"21ea25df-21a4-4a2d-9f33-c542ac34c074": 2, "0902a1cf-618f-45b7-b5b1-9e65b9ad113e": null}`
- 命中答案点：one shot
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：11456.089 ms

### 19. eval-019

- 问题：In the human motion intention paper, what transformation is applied to EEG/EMG signals before they enter AH-CNN-LSTM, and what roles do the CNN and LSTM play, respectively?
- 标准答案：先用 STFT 把 EEG/EMG 转为二维时频谱图；CNN 负责提取特征，LSTM 用于分类/建模序列信息并预测运动意图。
- 标准答案点：STFT、spectrogram、CNN、LSTM
- 模型答案：According to the sources, the EEG/EMG signals are converted into spectrogram images before being input to the AH-CNN-LSTM model [2][4]. Within the AH-CNN-LSTM model, the CNN is used for essential feature extraction, while the LSTM is used to classify human motion intentions accurately [7]. The LSTM also handles longer-range dependencies and sequential information [6].
- 正确证据首次排名：7
- 各标注证据排名：`{"b21a1939-ce2a-482e-84d3-80e31c5db029": null, "20dc87fe-68e0-44d7-80e5-4cb6c903f9a3": 7}`
- 命中答案点：spectrogram、CNN、LSTM
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：10697.831 ms

### 20. eval-020

- 问题：According to the writing guide, what is the core reproducibility goal of a Methodology section?
- 标准答案：It should describe exactly what was done and used, in enough precise detail that another reader can repeat the procedure and obtain the same result, while also showing care and justification for choices.
- 标准答案点：repeat exactly、same result、care、reasons
- 模型答案：According to the writing guide, the core reproducibility goal of a Methodology section is to describe what you did and used so that any reader can repeat exactly what you did and obtain exactly the same result [1].
- 正确证据首次排名：1
- 各标注证据排名：`{"0ff56d21-9be2-4c9e-b382-4d7ea7c29cb1": 1}`
- 命中答案点：repeat exactly、same result
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：9378.54 ms

### 21. eval-021

- 问题：How many diffusion steps are used in DDPM's main experiments, what is the linear beta range, and what are the reverse-network backbone and attention locations?
- 标准答案：T=1000；β 从 10^-4 线性增加到 0.02；反向网络使用类似 unmasked PixelCNN++ 的 U-Net、全程 group normalization，并在 16×16 特征图分辨率使用 self-attention。
- 标准答案点：T = 1000、10^-4、0.02、U-Net、16 x 16
- 模型答案：DDPM's main experiments use T = 1000 diffusion steps [4][5]. The forward process variances increase linearly from β₁ = 10⁻⁴ to β_T = 0.02 [4][5]. The reverse process is represented with a U-Net backbone similar to an unmasked PixelCNN++, with group normalization throughout and parameters shared across time via Transformer sinusoidal position embeddings [1][4]. Self-attention is used at the 16 × 16 feature map resolution [1][4].
- 正确证据首次排名：4
- 各标注证据排名：`{"bf18463d-d700-44d6-8039-8fdf0d899720": null, "141666e4-dc73-4579-afe5-62de65498204": 4}`
- 命中答案点：T = 1000、0.02、U-Net
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：9628.257 ms

### 22. eval-022

- 问题：In DDPM Table 2, under fixed variance, what are the FIDs of epsilon-prediction with the standard variational bound and epsilon-prediction with Lsimple, respectively?
- 标准答案：标准 variational bound + fixed isotropic variance 的 ε-prediction FID 为 13.51；Lsimple 的 ε-prediction FID 为 3.17。
- 标准答案点：13.51、3.17
- 模型答案：According to DDPM Table 2 in the source, for the epsilon-prediction (“ours”) parameterization with fixed isotropic variance:
- Trained with the standard variational bound: FID = 13.51 [1]
- Trained with L_simple: FID = 3.17 [1]
- 正确证据首次排名：1
- 各标注证据排名：`{"ccdd5832-5161-43d8-8b23-4679da3a6633": null, "141666e4-dc73-4579-afe5-62de65498204": 1}`
- 命中答案点：13.51、3.17
- 答案判断：答案点全部命中
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：20812.722 ms

### 23. eval-023

- 问题：In EEG2IM's ImageNet-40 generation ablation, what are the IS and FID for H only and for H+L+FiLM, respectively?
- 标准答案：仅高层 H：IS 15.99、FID 60.46；H+L+FiLM：IS 17.58、FID 52.84。
- 标准答案点：15.99、60.46、17.58、52.84
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"2e59e87f-320e-435f-ae7c-49d3d993eddb": null, "a4587b37-58eb-49f4-ab6a-c4b133cad607": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：9090.736 ms

### 24. eval-024

- 问题：In the ImageNet-4 classification ablation, how do Accuracy and F1-Score change from using only the time-domain feature T to using T+F+KD?
- 标准答案：T 为 Accuracy 90.58%、F1 90.55%；T+F+KD 为 Accuracy 92.55%、F1 92.39%。
- 标准答案点：90.58%、90.55%、92.55%、92.39%
- 模型答案：In the ImageNet-4 classification ablation, moving from the time-domain feature T alone to T+F+KD increases:

- Accuracy: from 90.58% to 99.95%
- F1-Score: from 90.55% to 99.93%

This is an improvement of about 9.37 percentage points in Accuracy and 9.38 percentage points in F1-Score [3].
- 正确证据首次排名：1
- 各标注证据排名：`{"a9b0c3a5-b0dd-4834-8e39-72586542fe55": 1, "a3cc4804-4b8b-4620-b933-285d935eab62": 2}`
- 命中答案点：90.58%、90.55%
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：错引：引用未命中标注证据
- 延迟：21976.721 ms

### 25. eval-025

- 问题：What FID and Accuracy does LMM-Large report for action-conditioned motion generation on HumanAct12 and UESTC, respectively?
- 标准答案：HumanAct12：FID 0.065±0.00、Accuracy 0.992±0.008；UESTC：FID 9.01±0.54、Accuracy 0.952±0.000。
- 标准答案点：0.065、0.992、9.01、0.952
- 模型答案：LMM-Large reports the following for action-conditioned motion generation:

- HumanAct12: FID 0.065±.00, Accuracy 0.992±.008 [1]
- UESTC: FID 9.01±0.54, Accuracy 0.952±.000 [1]
- 正确证据首次排名：1
- 各标注证据排名：`{"390fd8c0-5c25-4915-919c-58485ced6bb7": 1}`
- 命中答案点：0.065、0.992、9.01、0.952
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：8709.793 ms

### 26. eval-026

- 问题：What are the main training-step and learning-rate settings for LMM pretraining and fine-tuning?
- 标准答案：预训练使用 Adam，学习率 2×10^-4，80K iterations；微调先以 2×10^-4 训练 20K steps，再以 2×10^-5 训练 20K steps。
- 标准答案点：80K、2e-4、20K、2e-5
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：9
- 各标注证据排名：`{"47036e36-957b-4d8a-aae3-f09c8688291c": null, "215687f2-ba46-4b8f-86f2-c012defb7788": null, "bf3db0ed-3094-4fd2-92b7-7fc9c62e414e": null, "b64b34d6-eec2-4c1b-b920-0c43dae7f97d": null, "4d2bdb25-36b5-4a8a-9682-486ff8c731e4": 9}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 部分命中
- 引用判断：漏引：没有引用
- 延迟：9064.43 ms

### 27. eval-027

- 问题：In ACTOR's architecture ablation on UESTC, what are the FIDtest and Accuracy of the Transformer decoder and the autoregressive decoder, respectively?
- 标准答案：Transformer：FIDtest 23.43±2.20、Accuracy 91.1±0.3%；autoregressive decoder：FIDtest 60.10±4.87、Accuracy 88.4±0.6%。
- 标准答案点：23.43、91.1、60.10、88.4
- 模型答案：In the architecture ablation on UESTC (Table 2) [2]:

- **Transformer decoder** (the full ACTOR model): FID_test = **0.19±0.00**, Accuracy = **96.2±0.2** [2].
- **Autoregressive decoder** (“w/ autoreg. decoder”): FID_test = **2.62±0.01**, Accuracy = **88.0±0.5** [2].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"ebc90f9e-77be-4acc-878d-0d6183719244": null, "3b69cc80-3de1-486e-930a-fb42902e354b": null}`
- 命中答案点：无
- 答案判断：疑似错答：答案点均未命中
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：48421.063 ms

### 28. eval-028

- 问题：What are the FIDtr and Accuracy of ACTOR and Action2Motion on HumanAct12, respectively?
- 标准答案：ACTOR：FIDtr 0.12±0.00、Accuracy 95.5±0.8%；Action2Motion：FIDtr 2.46±0.08、Accuracy 92.3±0.2%。
- 标准答案点：0.12、95.5、2.46、92.3
- 模型答案：For HumanAct12:

- **ACTOR (ours):** FIDtr = 0.12±0.00, Accuracy = 95.5±0.8 [1].
- **Action2Motion :** FIDtr = 2.46±0.08, Accuracy = 92.3±0.2 [1].
- 正确证据首次排名：1
- 各标注证据排名：`{"c46ecd98-499e-4dff-9343-af1d3ad6a173": 1, "855bf099-49ae-4ca2-8f7d-891083cf37cd": 3}`
- 命中答案点：0.12、95.5、2.46、92.3
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：部分引用命中
- 延迟：8212.588 ms

### 29. eval-029

- 问题：What are the Accuracy and Sensitivity of IYSGA-AH-CNN-LSTM in Table 2 of the human motion intention paper?
- 标准答案：Accuracy 为 94.28513，Sensitivity 为 94.63087。
- 标准答案点：94.28513、94.63087
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"55c3d789-1786-47c4-a604-259af9c623bd": null, "d8a01bb8-2aa6-4f15-a98b-bef50e246a79": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：14692.615 ms

### 30. eval-030

- 问题：What computation times does the human motion intention paper report for AH-CNN-LSTM and AH-CNN-Res-LSTM, respectively?
- 标准答案：AH-CNN-LSTM 为 13.61 分钟，AH-CNN-Res-LSTM 为 13.09 分钟。
- 标准答案点：13.61 min、13.09 min
- 模型答案：The paper reports computation times of 13.61 minutes for AH-CNN-LSTM and 13.09 minutes for AH-CNN-Res-LSTM [1][2][4][5].
- 正确证据首次排名：1
- 各标注证据排名：`{"dc67257a-c683-40d8-8e97-c33818dc54a3": 1}`
- 命中答案点：13.61 min、13.09 min
- 答案判断：答案点全部命中
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：7947.782 ms

### 31. eval-031

- 问题：Based on DDPM's explanation of the objective and its experimental results, why does Lsimple achieve better sample quality even though it does not optimize the true variational bound?
- 标准答案：Lsimple 去掉了 Eq.12 中的权重，使小 t、低噪声的容易去噪项被降权，训练更关注大 t 的困难去噪任务；实验也显示真实 variational bound 有更好的 codelength，而 Lsimple 的样本质量最好（CIFAR-10 FID 3.17）。
- 标准答案点：down-weight small t、harder denoising、better codelength、best sample quality、FID 3.17
- 模型答案：Lsimple achieves better sample quality than training on the true variational bound because it is a reweighted objective that changes what the model focuses on, rather than optimizing the exact likelihood bound. Specifically, Lsimple discards the weighting in the true variational bound and down-weights loss terms corresponding to small timesteps t. These small-t terms train the network to denoise data with very small amounts of noise, so down-weighting them allows the network to focus on more difficult denoising tasks at larger t terms, which leads to better sample quality [1][5]. The paper explicitly states that this reweighting leads to better sample quality, while training on the true variational bound yields better codelengths, as expected [1][2]. Thus, the simplified objective trades likelihood accuracy for perceptually better samples, and is also the setting where the proposed ε-prediction parameterization performs much better than the baseline μ-prediction (which only works well when trained on the true variational bound) [4].
- 正确证据首次排名：3
- 各标注证据排名：`{"cf89610f-9c78-496c-852e-eba335c14955": 5, "2f5cb43a-45bf-478a-bbe7-2f4d6d151d12": 3}`
- 命中答案点：better codelength
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：部分引用命中
- 延迟：13489.041 ms

### 32. eval-032

- 问题：How are EEG2IM's high- and low-level semantics obtained, and how do the ablation results support the multi-level semantics plus FiLM design?
- 标准答案：高层语义通过冻结 ResNet50 教师的 response/feature distillation 学得类别信息；低层语义通过 EEG encoder 与 image autoencoder 联合训练对齐颜色、轮廓等细节。生成消融中 ImageNet-40 从 H 的 15.99/60.46 提升到 H+L+FiLM 的 17.58/52.84（IS↑/FID↓），支持多层语义和 FiLM 的共同作用。
- 标准答案点：knowledge distillation、joint training、17.58、52.84、FiLM
- 模型答案：According to the provided sources, EEG2IM obtains **high-level semantics** through knowledge distillation: a pre-trained image model transfers high-level semantic knowledge to the EEG encoder, improving its ability to capture category-specific features [6]. The sources also indicate that EEG2IM uses both time-domain and frequency-domain EEG data, and that combining these with knowledge distillation improves performance [5][6]. The sources do not explicitly describe the exact procedure for obtaining the **low-level semantics**; they only state that low-level features contribute crucial structural and fine-grained details to the generated images [7].

The ablation results support the multi-level semantics plus FiLM design as follows:

- Using **only high-level features** results in a lower Inception Score and higher FID than using **both high-level and low-level features**, showing that low-level features add important structural and fine-grained information [7].
- Adding **FiLM modulation** on top of the high-level + low-level feature fusion yields the highest IS and lowest FID, demonstrating that FiLM effectively guides the diffusion model to generate more coherent and detailed images [7][8].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"8c8f1f90-529a-4b57-a181-0073f9f4dc57": null, "1e52251d-c6d9-4046-a26c-fe1b1f85d66c": null, "a4587b37-58eb-49f4-ab6a-c4b133cad607": null, "39c908e3-bd74-47a3-bb5b-604e220ffe94": null}`
- 命中答案点：knowledge distillation、FiLM
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：19802.576 ms

### 33. eval-033

- 问题：How does LMM progress from resolving inconsistent data formats to enabling the model to handle missing body parts and multimodal conditions?
- 标准答案：数据侧用 TOMATO 统一表示并拆成 10 个身体部分、显式标记缺失部位；模型侧 ArtAttention 用 body-part attention 处理空间/部位关系，并用 temporal attention 融合时间相关性与 ImageBind 编码的多模态条件；预训练再通过随机 mask 学会利用可见部位恢复缺失部位。
- 标准答案点：TOMATO、10 body parts、ArtAttention、random masking
- 模型答案：LMM addresses inconsistent data formats by first consolidating heterogeneous motion datasets into a unified dataset, MotionVerse, and aligning all motion data to a unified intermediate format while converting condition signals into token sequences that are closer in feature space [6][7]. To resolve format inconsistency specifically, LMM introduces a body part-aware motion generation model that divides the human body into 10 segments and decomposes motion data from different datasets into relatively independent segments, allowing the model to leverage knowledge across datasets [1].

Building on this foundation, LMM’s architecture enables handling missing body parts and multimodal conditions through its specialized attention mechanism, ArtAttention. ArtAttention incorporates multi-conditioning, spatial-temporal independence, and mask injection, which allows distinct control over different body parts and explicitly supports allowance for missing body parts [1][8]. The mask injection capability is further strengthened during training: LMM applies random masks to sequences and body parts in various ways, improving control over the continuity of body part movements and robustness to varying frame rates [2]. This lets the model handle scenarios where entire body parts are missing, although its intermediate representation struggles with cases where only individual keypoints within a body part are missing [4]. Finally, LMM’s multi-conditioning design enables it to process multi-modal inputs simultaneously, allowing it to handle multimodal conditions and even accomplish unseen tasks [3][8].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"142b9edd-52e8-461e-bc21-3fb4d1cbd4b0": null, "b58ade50-546c-4b8c-8d76-d1c73da4a4ba": null, "ad861fb5-36e5-441b-8d19-335982d1ad0d": null}`
- 命中答案点：ArtAttention
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：12130.541 ms

### 34. eval-034

- 问题：Why does the paper argue that LMM-Tiny underperforms FineMoGen on HumanML3D, while LMM-Large improves substantially?
- 标准答案：论文把 Tiny 的劣势归因于多数据、多任务训练带来的复杂性与任务间 trade-off，且统一表示维度 669 高于 HumanML3D 常用的 263，增加了小模型学习难度；更大模型具有更强容量，从大规模数据中学到更稳健的 motion prior，因此 Large 表现更好。
- 标准答案点：diverse data、trade-offs、263、669、larger model
- 模型答案：The paper argues that LMM-Tiny underperforms FineMoGen on HumanML3D because LMM-Tiny, despite sharing a similar structure with FineMoGen, faces significant challenges from the large amounts of diverse data and the tradeoffs across different tasks during model training—challenges that are especially hard for smaller models [1]. Additionally, the paper notes that while HumanML3D uses a representation dimension of 263, LMM uses 669, which significantly increases the learning difficulty for LMM-Tiny [1]. In contrast, LMM-Large substantially improves and surpasses existing works in accuracy and fidelity while maintaining comparable diversity [1]. The parameter counts also differ greatly: LMM-Tiny has 90M parameters, whereas LMM-Large has 760M parameters [6].
- 正确证据首次排名：1
- 各标注证据排名：`{"dfd2ee55-c301-4dae-9706-81ff1388c01e": 1}`
- 命中答案点：diverse data、263、669
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：12901.611 ms

### 35. eval-035

- 问题：Why do ACTOR's sequence-level latent representation and non-autoregressive decoder both support variable-length generation and outperform the autoregressive version in the ablation study?
- 标准答案：ACTOR 用单个 sequence-level z 表示整段动作，decoder 通过目标长度对应的 positional encodings 一次性查询整段序列，因此长度可直接控制且没有逐帧误差传播；论文指出 autoregressive 训练使用 teacher forcing，训练/测试产生 gap，UESTC 消融中其 FIDtest 60.10，明显差于 non-autoregressive Transformer 的 23.43。
- 标准答案点：sequence-level、positional encodings、teacher forcing、60.10、23.43
- 模型答案：ACTOR’s sequence-level latent representation and non-autoregressive decoder support variable-length generation and outperform the autoregressive version for several linked reasons:

1. **Sequence-level latent representation instead of frame-level**: ACTOR is described as the first action-conditioned sequence-level embedding, whereas the closest work, Action2Motion, uses an autoregressive approach with a frame-level latent representation. Getting a sequence-level embedding requires pooling the time dimension, and this new combination of Transformers and VAEs significantly improves performance over baselines [6]. A single latent vector represents the whole motion, which allows the decoder to synthesize the full sequence rather than frame-by-frame.

2. **Non-autoregressive, full-sequence decoding with positional encodings**: ACTOR’s novelty is to provide positional encodings to the decoder and output the full sequence at once. This enables generation of variable-length sequences without the common autoregressive problems of regressing to the mean pose and drift [5]. At generation time, the duration is controlled by specifying a sequence of positional encodings to the decoder, so different durations can be synthesized and this also trivially increases diversity [4]. The ablation study confirms that this sequence-level decoding strategy is key: the Transformer with sequence-level decoding outperforms an autoregressive Transformer decoder [1].

3. **Avoiding the teacher-forcing train/test gap**: The autoregressive version uses teacher forcing at training time, i.e., feeding the ground-truth pose for the previous frame. This creates a gap at test time, where the model produced poor autoencoding reconstructions—for example, decoding a left-hand waving encoding into a right-hand waving motion [2]. The non-autoregressive, sequence-level approach avoids this error-accumulation and train/test mismatch, which contributes to its better performance in the ablation study [1][2].

In the ablation study, the full Transformer model outperforms both fully-connected and GRU encoder-decoder architectures by a large margin, and the sequence-level decoding strategy is specifically shown to be key to the improvement over the autoregressive Transformer variant [1].
- 正确证据首次排名：1
- 各标注证据排名：`{"21ea25df-21a4-4a2d-9f33-c542ac34c074": null, "a2252513-c2ca-4134-a1cd-3e4f3f397b54": 1, "3b69cc80-3de1-486e-930a-fb42902e354b": null}`
- 命中答案点：sequence-level、positional encodings、teacher forcing
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：15196.017 ms

### 36. eval-036

- 问题：How does ACTOR use the generative model's latent space for motion denoising, and what experimental observation supports this use?
- 标准答案：把真实的噪声运动先编码到学习到的动作 latent，再解码即可得到更干净的运动。UESTC 的动作识别实验显示，对 encoded-decoded 的 denoised motion 训练/测试可以提高识别表现，说明 latent 表示具有去噪作用。
- 标准答案点：encoding-decoding、cleaner motions、action recognition、denoising
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"d61dbab5-0762-4130-8771-783815ddd18a": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：11093.06 ms

### 37. eval-037

- 问题：Summarize the two-stage multimodal prediction pipeline in the human motion intention paper, and explain what objective IYSGA optimizes in each of the two branches.
- 标准答案：阶段 1：EEG/EMG→STFT spectrogram→AH-CNN-LSTM；阶段 2：sensor measures→AH-CNN-Res-LSTM。两条分支都用 IYSGA 调节隐藏神经元数和 epoch 等参数，目标是最大化 MCC 并降低 FDR。
- 标准答案点：EEG/EMG、STFT、AH-CNN-LSTM、sensor、AH-CNN-Res-LSTM、MCC、FDR
- 模型答案：The paper proposes a two-stage multimodal pipeline for human motion intention prediction:

- **Stage 1 (EEG/EMG branch):** EEG/EMG signals are converted into spectrogram images, which are fed into an AH-CNN-LSTM network for motion intention prediction [1]. In this branch, IYSGA tunes the CNN and LSTM model parameters [1][5].
- **Stage 2 (sensor branch):** Sensor measurement data are used as input to an AH-CNN-Res-LSTM network for human motion intention prediction [1][6]. In this branch, IYSGA tunes parameters such as the number of epoch counts and hidden neuron count in the CNN and Res-LSTM models [2][6].

In both branches, IYSGA optimizes the models to **maximize the Matthews Correlation Coefficient (MCC)** and **reduce the False Discovery Rate (FDR)** [1][5][6]. Specifically, in the AH-CNN-LSTM branch it tunes the CNN and LSTM parameters for this objective [1][5], and in the AH-CNN-Res-LSTM branch it tunes the CNN and Res-LSTM parameters (e.g., epoch and hidden neuron counts) to improve MCC and decrease FDR [2][6].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"f042c88e-81ae-4ace-9424-ce24d58ff2f7": null, "0a2dd90f-dadc-4064-864b-2ea2cd0f68e8": null}`
- 命中答案点：EEG/EMG、AH-CNN-LSTM、sensor、AH-CNN-Res-LSTM、MCC、FDR
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：13077.476 ms

### 38. eval-038

- 问题：How does the writing guide explain the mirror relationship between the Introduction and the Discussion/Conclusion in the overall structure of a paper?
- 标准答案：Introduction 把读者带入研究：建立主题重要性、给背景、定位研究空白并描述当前工作；Discussion/Conclusion 反向把读者带出论文：回顾方法/结果、把结果放回已有研究地图，提炼贡献、局限、未来工作和应用。
- 标准答案点：move into、move out、research map、limitations、future work
- 模型答案：The writing guide explains the mirror relationship by describing the overall structure as symmetrical, with many elements of the Introduction recurring in the Discussion/Conclusion in approximately reverse order [3][4]. Specifically, the Introduction moves from a broad, general focus toward the narrower “report” section, while the Discussion/Conclusion moves away from that narrow section back to a wider, more general focus [3][6]. It also notes that the Introduction needs an opening sentence to “get in,” and the Discussion/Conclusion needs a way to “get out”; similarly, the end of the Introduction creates an interface with the central report section, and the Discussion/Conclusion reverses that interface as it moves out of the central section [2][4]. In addition, the Introduction locates a gap or problem in existing research, and the Discussion/Conclusion is expected to state to what extent that gap has been responded to or the problem solved [1]. Finally, while the Introduction gives a picture of the current research map, the Discussion/Conclusion shows how and where the study fits into that map and how it changes or affects it [5].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"fcd28894-bd74-4db0-bfd2-dbe17a4a1ba2": null, "ac1d5740-3ad7-4eeb-9d22-54f4e451118b": null}`
- 命中答案点：research map
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：10813.641 ms

### 39. eval-039

- 问题：DDPM and EEG2IM both use diffusion generation, but how do their conditioning information fundamentally differ?
- 标准答案：DDPM 论文的核心 CIFAR-10 模型是无条件图像扩散，从噪声通过时间步条件的 εθ 反演；EEG2IM 则把 EEG 提取的高层类别语义和低层细节语义作为外部条件，通过 FiLM 注入 U-Net，从而实现 EEG 条件的细粒度图像生成。
- 标准答案点：unconditional、EEG、high-level、low-level、FiLM
- 模型答案：The key difference is in the level and richness of the conditioning signal:

- Traditional DDPM-based EEG-to-image methods are **category-driven**: they condition generation on high-level semantic information such as object categories [5]. Earlier approaches using DDPMs were used for category-level image generation [5].

- EEG2IM instead conditions the diffusion model on **multi-level EEG semantic features**: it combines high-level semantic features (e.g., object categories) with low-level features that capture fine-grained attributes such as color and contour [2][3]. These multi-level features are injected into the diffusion model via Feature-wise Linear Modulation (FiLM), allowing both semantic consistency and fine details to be preserved [3].

Thus, while DDPMs typically use only high-level category information as conditioning, EEG2IM fundamentally conditions on both high-level semantics and low-level fine-grained attributes to generate more detailed images [2][3][5].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"f80fdf91-3cff-463a-9a65-627fdae3bd54": null, "26fa2f6e-f2c7-406c-930a-a8efbad4a5a5": null}`
- 命中答案点：EEG、high-level、low-level、FiLM
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：12767.399 ms

### 40. eval-040

- 问题：Compare LMM and ACTOR in terms of generative backbone, conditioning scope, and task breadth.
- 标准答案：LMM is a Diffusion Transformer generalist model with ArtAttention, designed for multiple modalities and many motion tasks in MotionVerse. ACTOR is a Transformer-based conditional VAE centered on categorical action conditioning and variable-length 3D motion generation from a sequence-level latent.
- 标准答案点：Diffusion Transformer、ArtAttention、conditional VAE、categorical action、generalist
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：2
- 各标注证据排名：`{"0f8f8d42-d30d-4d95-99cb-d0a43f8f71bf": 2, "2956f056-2d15-46f0-aa47-45a204711288": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 部分命中
- 引用判断：漏引：没有引用
- 延迟：7523.584 ms

### 41. eval-041

- 问题：EEG2IM and the human motion intention prediction paper both process EEG. What are their respective target outputs, and how is EEG represented in each?
- 标准答案：EEG2IM 的目标是由 EEG 条件生成细粒度图像，显式学习高层/低层语义并与图像特征对齐；运动意图论文的目标是分类/预测人类运动意图，把 EEG/EMG 先用 STFT 转为 spectrogram，再交给 CNN-LSTM 分支。
- 标准答案点：image generation、multi-level semantics、motion intention、STFT、spectrogram
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"9f079d90-5496-44e1-aad5-2f7f6b21e0ec": null, "b21a1939-ce2a-482e-84d3-80e31c5db029": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：9257.635 ms

### 42. eval-042

- 问题：LMM and ACTOR both perform action-to-motion generation on UESTC. What UESTC FIDtest does each paper report, and what comparison caveat must be considered?
- 标准答案：LMM Table 9 报告 LMM-Large 的 UESTC FID 为 9.01±0.54；ACTOR 报告 FIDtest 23.43±2.20。两篇都说明使用 UESTC test split，但跨论文比较仍应注意实现、预处理和评估模型可能不同，不能只凭一个数断言完全公平。
- 标准答案点：9.01、23.43、test split、comparison caveat
- 模型答案：From the LMM paper’s reported table on UESTC action-to-motion:

- **ACTOR**: UESTC FID = **23.43±2.20** [2]
- **LMM-Tiny**: UESTC FID = **20.16±1.78** [7]
- **LMM-Small**: UESTC FID = **14.28±1.14** [3]
- **LMM-Base**: UESTC FID = **10.36±0.60** [8]

The LMM-Large model is described as achieving the best FID and Accuracy in the action-conditioned task, but its numeric UESTC FID is not present in the provided sources [4].

**Comparison caveat:** For the action-to-motion task, higher diversity and multimodality—which LMM tends to exhibit due to exposure to more data—do **not necessarily indicate better performance** [4]. Therefore, when comparing LMM against ACTOR, one should not interpret larger diversity/multimodality values as an automatic advantage.
- 正确证据首次排名：9
- 各标注证据排名：`{"390fd8c0-5c25-4915-919c-58485ced6bb7": 9, "c4ddf1f8-f078-49d3-8320-9648a5ee6bf0": null, "36e84b83-4a53-4f07-80c3-37cd8f1b016a": null, "4f2837f0-941e-4884-b624-750f34e8b2f9": null, "638b2dfa-ed47-4b07-bd0b-9053f725f3c7": null, "2d70f06b-a7c3-4b76-9b91-aa1ad5f05ffd": null, "ebc90f9e-77be-4acc-878d-0d6183719244": null}`
- 命中答案点：23.43、comparison caveat
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：错引：引用未命中标注证据
- 延迟：25200.195 ms

### 43. eval-043

- 问题：How do the diffusion backbones of DDPM and LMM adapt to images and human motion, respectively?
- 标准答案：DDPM 使用图像 U-Net，带 group normalization、时间位置编码和 16×16 self-attention；LMM 使用面向运动的 Diffusion Transformer，并用 ArtAttention 把身体部位空间注意力与时间注意力分开，同时处理多模态条件和不同帧率。
- 标准答案点：U-Net、group normalization、Diffusion Transformer、ArtAttention
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"141666e4-dc73-4579-afe5-62de65498204": null, "b58ade50-546c-4b8c-8d76-d1c73da4a4ba": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：17640.385 ms

### 44. eval-044

- 问题：According to the abstract principles in Science Research Writing, which parts of the DDPM abstract serve the functions of method/contribution and key results?
- 标准答案：写作指南要求摘要成为独立、自包含的论文表示，并从正文提炼核心内容。DDPM 摘要的方法/贡献部分是加权 variational bound 及其与 denoising score matching/Langevin dynamics 的联系；关键结果是 CIFAR-10 的 IS 9.46、FID 3.17，以及 LSUN 样本质量比较。
- 标准答案点：standalone、weighted variational bound、denoising score matching、9.46、3.17
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：10
- 各标注证据排名：`{"c05c6fa5-f54e-4bd1-984d-ae423ec5907d": 10, "f80fdf91-3cff-463a-9a65-627fdae3bd54": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 部分命中
- 引用判断：漏引：没有引用
- 延迟：8730.825 ms

### 45. eval-045

- 问题：EEG2IM and ACTOR are both conditional generative models. What are their respective conditions, latent representations, and outputs?
- 标准答案：EEG2IM 的条件来自 EEG 的高层类别语义与低层视觉细节，经过 FiLM 调制扩散 U-Net，输出图像；ACTOR 的条件是 categorical action 和目标 duration，使用 VAE 的 sequence-level latent z 与 Transformer decoder，输出 SMPL 3D human motion sequence。
- 标准答案点：EEG condition、FiLM、image、categorical action、latent z、SMPL motion
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"9f079d90-5496-44e1-aad5-2f7f6b21e0ec": null, "2956f056-2d15-46f0-aa47-45a204711288": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：8709.653 ms

### 46. eval-046

- 问题：LMM and EEG2IM both emphasize multilevel or multimodal conditioning. What mechanism does each use to feed conditions into the generative network?
- 标准答案：LMM 先用 ImageBind 把 text/speech/music/video 变成 token sequences，再由 ArtAttention 的 temporal attention 与 motion features 融合；EEG2IM 从 EEG 获得高低层语义 embedding，并通过 FiLM 生成 γ/β 在 U-Net 各层调制特征图。
- 标准答案点：ImageBind、token sequences、ArtAttention、FiLM、U-Net
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"15bc73fa-5f2b-4507-9c7d-0dbfd148d652": null, "26fa2f6e-f2c7-406c-930a-a8efbad4a5a5": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：13652.605 ms

### 47. eval-047

- 问题：Then why does it achieve better sample quality instead?
- 标准答案：因为 Lsimple 会相对降权小 t 的低噪声项，让模型更关注大 t 的困难去噪任务；论文实验显示这提高了 sample quality，尽管真实 variational bound 的 codelength 更好。
- 标准答案点：down-weight small t、harder denoising、best sample quality
- 模型答案：**What is L_simple in DDPM?**

L_simple is the simplified training objective proposed in the paper, defined as [6]:

L_simple(θ) := E_{t, x0, ε} [ || ε − ε_θ(√(ᾱ_t) x0 + √(1 − ᾱ_t) ε, t) ||² ]

It is a simplified, unweighted variant of the variational bound. Compared to the standard variational bound, it discards the weighting in the original objective and instead predicts the noise ε directly, given a noisy image and the timestep t [6, 7].

**Why does it achieve better sample quality?**

The simplified objective achieves better sample quality for two related reasons:

1. **It down-weights easy denoising tasks.** The simplified objective causes loss terms corresponding to small t (i.e., denoising data with very small amounts of noise) to be down-weighted. Since these are relatively easy tasks, down-weighting them allows the network to focus on the more difficult denoising tasks at larger t terms. This reweighting leads to better sample quality [7].

2. **Empirical findings show a trade-off.** The authors found that training on the true variational bound yields better codelengths (negative log-likelihood), but the simplified objective yields the best sample quality [4, 5]. In ablation experiments (Table 2), predicting ε with the simplified objective (L_simple) significantly outperformed other parameterizations and objectives in terms of FID score [6].
- 正确证据首次排名：3
- 各标注证据排名：`{"cf89610f-9c78-496c-852e-eba335c14955": 3, "2f5cb43a-45bf-478a-bbe7-2f4d6d151d12": 10}`
- 命中答案点：best sample quality
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：错引：引用未命中标注证据
- 延迟：31102.344 ms

### 48. eval-048

- 问题：What is the result after adding FiLM?
- 标准答案：在 ImageNet-4 的生成消融中，H+L+FiLM 的 IS=8.79、FID=19.49。
- 标准答案点：8.79、19.49
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"6dfc92af-6080-4d80-a09b-cdb71eb52dc7": null}`
- 命中答案点：无
- 答案判断：错判：可回答题被拒答
- 检索判断：Top-10 未命中
- 引用判断：漏引：没有引用
- 延迟：21546.771 ms

### 49. eval-049

- 问题：How exactly does it handle these inconsistencies?
- 标准答案：先统一到 TOMATO 表示并拆成 10 个身体部分、标记缺失部位；不同帧率通过真实时间建模和预训练随机下采样适配，测试时再用 translator 转成各 benchmark 所需格式。
- 标准答案点：TOMATO、10 parts、real time、different frame rates
- 模型答案：是的，根据提供的来源，MotionVerse 确实需要解决不同数据集在运动表示格式、关键点数量/身体部位是否存在以及帧率等方面的不一致问题，但来源并未用“最大问题”这一表述，而是将其作为构建统一基准所面临的挑战之一。例如，来源指出需要“align the diverse formats of motion data”[2]，并在统一格式中“annotations indicating which body parts are present in each sequence”以应对不同数据集身体部位缺失的情况[2]；同时，在模型训练中采用了“random frame rate augmentation”来增强对不同帧率的鲁棒性[4]，说明帧率差异也是处理对象。

具体处理方式如下：

1. **统一中间表示（TOMATO）**：先将所有运动序列转换为 TOMATO 表示，并进一步将整个人体分成 10 个独立身体部分，作为统一的运动表示；同时保留“哪些身体部分存在”的注释，以解决不同数据集关键点数量不一致（尤其是某些身体部分缺失）的问题[2][6]。

2. **训练表示转换器**：在测试阶段，训练一系列表示转换器，将统一的 TOMATO 表示转换回各个数据集各自特定的运动表示，从而在训练时能够混合使用不同格式的数据源[2]。

3. **统一多模态条件信号**：对于文本、音频等多模态条件输入，使用 ImageBind 将它们转换为跨模态的统一特征，以便与运动数据一起输入模型[6]。

4. **模型层面的处理**：在大规模运动模型（LMM）中，采用身体部位感知的生成模型，将人体分成 10 段，并通过 ArtAttention 机制（支持多条件、时空独立性、掩码注入）对不同身体部位进行独立控制，从而利用来自不同数据集的知识[3]。在训练中，通过随机帧率增强来适应帧率不一致，并通过随机掩码来增强对身体部位运动连续性的控制[4]。

综合这些步骤，MotionVerse 从数据预处理（TOMATO 统一表示 + 转换器）、条件信号统一（ImageBind）以及模型训练（身体部位感知 + 帧率/掩码增强）三个层面来处理这些不一致性。
- 正确证据首次排名：2
- 各标注证据排名：`{"142b9edd-52e8-461e-bc21-3fb4d1cbd4b0": 2, "bddf75bc-da86-44fc-a34f-c96267a2d907": null}`
- 命中答案点：TOMATO
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 部分命中
- 引用判断：部分引用命中
- 延迟：36414.148 ms

### 50. eval-050

- 问题：Why does it avoid autoregressive decoding?
- 标准答案：ACTOR generates the whole sequence in one shot from the sequence-level latent and time queries. The paper reports that autoregressive teacher forcing creates a train-test gap and performs worse in ablation.
- 标准答案点：one shot、teacher forcing、train-test gap
- 模型答案：ACTOR avoids autoregressive decoding because autoregressive approaches regress to the mean pose after some time and are subject to drift [4]. In addition, an autoregressive Transformer decoder that uses teacher forcing at training time creates a gap with test time, leading to poor autoencoding reconstructions [2][3]. Instead, ACTOR’s decoder outputs the full sequence at once from the single latent vector [1], and this sequence-level decoding strategy is key to obtaining an improvement with Transformers [2].
- 正确证据首次排名：1
- 各标注证据排名：`{"a2252513-c2ca-4134-a1cd-3e4f3f397b54": 2, "21ea25df-21a4-4a2d-9f33-c542ac34c074": 1}`
- 命中答案点：teacher forcing
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 全命中
- 引用判断：所需引用全部命中
- 延迟：20817.097 ms

### 51. eval-051

- 问题：What transformation is first applied in the signal branch?
- 标准答案：EEG/EMG 信号先通过 STFT 转成二维时频 spectrogram images，再输入 AH-CNN-LSTM。
- 标准答案点：STFT、spectrogram
- 模型答案：In the signal branch, the first transformation applied is converting the EEG/EMG signals into spectrogram images [5]. These spectrogram images are then used as input to the AH-CNN-LSTM framework [5].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"b21a1939-ce2a-482e-84d3-80e31c5db029": null}`
- 命中答案点：spectrogram
- 答案判断：部分正确：仅命中部分答案点
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：17902.302 ms

### 52. eval-052

- 问题：What is its structural relationship to the Introduction?
- 标准答案：两者近似镜像：Introduction 逐步把读者带入研究并定位当前工作，Discussion/Conclusion 则反向把读者从具体结果带向更广的研究地图、贡献、局限和未来工作。
- 标准答案点：mirror、move into、move out
- 模型答案：The Discussion has a structural relationship to the Introduction that is essentially symmetrical and reverse-ordered: **many elements of the Introduction occur again in the Discussion/Conclusion in approximately reverse order** [1][2]. The Introduction moves from a broad, general focus into the narrower "report" section, while the Discussion/Conclusion moves away from that narrow section back to a wider, more general focus [1][2][4]. The Discussion looks back at the points made in the Introduction on the basis of the information in the central report section [3]. It revisits the research map and the gap or problem described in the Introduction, and shows how the study responds to that gap or solves that problem [7]. The end of the Introduction creates an interface with the present paper to move the reader into the central report; the Discussion reverses this by beginning with revisiting some aspect of the work to move the reader away from the central report [8].
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{"fcd28894-bd74-4db0-bfd2-dbe17a4a1ba2": null}`
- 命中答案点：无
- 答案判断：疑似错答：答案点均未命中
- 检索判断：Top-10 未命中
- 引用判断：错引：引用未命中标注证据
- 延迟：17555.148 ms

### 53. eval-053

- 问题：How many hours does the DDPM paper report for training CIFAR-10 on an RTX 2060?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：8285.311 ms

### 54. eval-054

- 问题：What classification accuracy and FID does EEG2IM report on CIFAR-10?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：9297.608 ms

### 55. eval-055

- 问题：Does LMM report an FID for EEG-conditioned motion generation? If so, what is the value?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：7266.395 ms

### 56. eval-056

- 问题：What R-Precision and FID does ACTOR report for text-to-motion on HumanML3D?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：8176.469 ms

### 57. eval-057

- 问题：What EEG/EMG signal sampling frequency in Hz is reported in the human motion intention paper?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：8210.972 ms

### 58. eval-058

- 问题：Which Transformer or large language model does Science Research Writing recommend for automatically generating a paper?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：9140.974 ms

### 59. eval-059

- 问题：Among these six materials, which one reports peak GPU memory on an RTX 2060, and what is the value?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：10594.731 ms

### 60. eval-060

- 问题：What CUDA version and specific GPU model were used to train the diffusion model in EEG2IM?
- 标准答案：（不可回答）
- 标准答案点：无（不可回答题）
- 模型答案：Insufficient evidence in the provided sources.
- 正确证据首次排名：未进 Top-10
- 各标注证据排名：`{}`
- 命中答案点：无
- 答案判断：正确拒答
- 检索判断：不适用（不可回答）
- 引用判断：无引用
- 延迟：8251.164 ms
