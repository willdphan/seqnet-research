import torch
import torch.nn as nn
import numpy as np
import torch.nn.functional as F

# DEFAULT SEQNET: python3 /Users/williamphan/Desktop/developer/projects/seqNet/main.py --mode train --pooling seqnet --dataset nordland-sw --seqL 10 --w 5 --outDims 4096 --expName "w5" --nocuda

# SEQNET-MIX: python3 main.py --mode train --pooling seqnet_mix --dataset nordland-sw --seqL 10 --w 5 --outDims 4096 --expName "w5" --nocuda
# SEQNET-MIX: python3 /Users/williamphan/Desktop/developer/projects/seqNet/train.py --mode train --pooling seqnet_mix --dataset nordland-sw --seqL 10 --w 5 --outDims 4096 --expName "w5" --nocuda

# parser.add_argument('--pooling', type=str, default='seqnet', help='type of pooling to use', choices=[ 'seqnet', 'smooth', 'delta', 'single','single+seqmatch', 's1+seqmatch'])
# parser.add_argument('--seqL', type=int, default=5, help='Sequence Length')
# parser.add_argument('--w', type=int, default=3, help='filter size for seqNet')

"""
ORIGINAL SEQNET
"""

class seqNet(nn.Module):
    def __init__(self, inDims, outDims, seqL, w=5):

        super(seqNet, self).__init__()
        self.inDims = inDims
        self.outDims = outDims
        self.w = w 
        self.conv = nn.Conv1d(inDims, outDims, kernel_size=self.w)

    def forward(self, x):
        # print(f"X Shape: {x.shape}")
        # Input X Shape: torch.Size([24, 10, 4096]) - [batch size, sequence length, dimensions]
        
        if len(x.shape) < 3:
            x = x.unsqueeze(1) # convert [B,C] to [B,1,C]
        # print("SeqNet Input",x.shape) # [24, 10, 4096]
        x = x.permute(0,2,1) # from [B,T,C] to [B,C,T]
        # print("After permute", x.shape) # [24, 4096, 10]
        seqFt = self.conv(x)
        # print("After conv", seqFt.shape) # [24, 4096, 6]
        seqFt = torch.mean(seqFt,-1) # Average pooling over the temporal dimension
        # print("Sequence Feature Shape",seqFt.shape)
        # Sequence Feature Shape torch.Size([24, 4096]) - [batch size, output feature dimensions]

        return seqFt
    
"""
The Delta class highlights how feature values change over a sequence. It does this by using a special set of weights: 

- earlier parts of the sequence get negative weights
- later parts get positive weights

It calculates how features change over time within a sequence. — whether they tend to increase, decrease, or stay relatively constant over the sequence.
"""
    
class Delta(nn.Module):
    def __init__(self, inDims, seqL):
        super(Delta, self).__init__()
        self.inDims = inDims  # Number of input dimensions
        # Create a weighting vector to compute the delta (change) across the sequence
        self.weight = (np.ones(seqL, np.float32)) / (seqL / 2.0)
        self.weight[:seqL // 2] *= -1  # Negative weights for the first half, positive for the second
        self.weight = nn.Parameter(torch.from_numpy(self.weight), requires_grad=False)  # Convert to a tensor and set as a non-trainable parameter

    def forward(self, x):
        # Rearrange dimensions: [B,T,C] to [B,C,T] to align with weight vector for matrix multiplication
        x = x.permute(0, 2, 1)
        # Apply the weighting vector to compute the delta across the sequence for each feature
        delta = torch.matmul(x, self.weight)

        return delta

"""
SEQNET-MIX
"""

class FeatureMixerLayer(nn.Module):
    def __init__(self, in_dim, mlp_ratio=1):
        super().__init__()
        self.mix = nn.Sequential(
            nn.LayerNorm(in_dim),
            nn.Linear(in_dim, int(in_dim * mlp_ratio)),
            nn.ReLU(),
            nn.Linear(int(in_dim * mlp_ratio), in_dim),
        )

        for m in self.modules():
            if isinstance(m, (nn.Linear)):
                nn.init.trunc_normal_(m.weight, std=0.02)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(self, x):
        return x + self.mix(x)


class MixVPR(nn.Module):
    def __init__(self,
        in_channels=1024,
        in_h=20,
        in_w=20,
        out_channels=1024,
        mix_depth=4,
        mlp_ratio=1,
        out_rows=4) -> None:
        super().__init__()

        self.in_h = in_h # height of input feature maps
        self.in_w = in_w # width of input feature maps
        self.in_channels = in_channels # depth of input feature maps
        
        self.out_channels = out_channels # depth wise projection dimension
        self.out_rows = out_rows # row wise projection dimesion

        self.mix_depth = mix_depth # L the number of stacked FeatureMixers
        self.mlp_ratio = mlp_ratio # ratio of the mid projection layer in the mixer block

        hw = in_h*in_w
        self.mix = nn.Sequential(*[
            FeatureMixerLayer(in_dim=hw, mlp_ratio=mlp_ratio)
            for _ in range(self.mix_depth)
        ])
        self.channel_proj = nn.Linear(in_channels, out_channels)
        self.row_proj = nn.Linear(hw, out_rows)

    def forward(self, x):
        x = x.flatten(2)
        print(f"flattened, {x.shape}")
        x = self.mix(x)
        print(f"after mixers, {x.shape}")
        x = x.permute(0, 2, 1)
        print(f"after permute, {x.shape}")
        x = self.channel_proj(x)
        print(f"after channel proj, {x.shape}")
        x = x.permute(0, 2, 1)
        print(f"after second permute, {x.shape}")
        x = self.row_proj(x)
        print(f"after row proj, {x.shape}")
        x = F.normalize(x.flatten(1), p=2, dim=-1)
        print(f"after normalize, {x.shape}")
        return x

class seqNet_mix(nn.Module):
    def __init__(self, inDims, outDims, seqL, w=64):
            super().__init__()
            self.inDims = inDims
            self.outDims = outDims
            self.w = w
            self.mixvpr = MixVPR(
                in_channels=1,
                in_h=w,
                in_w=w,
                out_channels=outDims,
                mix_depth=4,
                mlp_ratio=1,
                out_rows=1  # Set to 1 as there's no row projection needed for single feature map processing
            )

    def forward(self, x):
            batch_size, seq_len, features = x.size()
            processed_batches = []

            for b in range(batch_size):
                processed_seqs = []

                for i in range(seq_len):
                    print(f"input, {x.shape}") # [24, 10 ,4096]
                    # Reshape each feature vector in the sequence to a 1x64x64 format
                    x_seq = x[b, i].view(1, 1, self.w, self.w)
                    print(f"after reshape, {x_seq.shape}")

                    # Process through MixVPR
                    x_processed = self.mixvpr(x_seq)
                    print(f"after mixvpr, {x_processed.shape}")
                    processed_seqs.append(x_processed.squeeze())

                # Stack processed sequences and store them
                x_batch_processed = torch.stack(processed_seqs, dim=0)
                processed_batches.append(x_batch_processed)

            # Stack all processed batches to get back to the original format [24, 10, 4096]
            x_final = torch.stack(processed_batches, dim=0)
            return x_final

def main():
    x = torch.randn(24, 10 ,4096)
    # x = torch.randn(1, 4096, 1 ,1)
    # x = torch.randn(1, 1024, 20, 20) # w
    # [1, 4096, 64, 64] - works for mixvpr
    # [1, 1024, 20, 20] - when processed through mixvpr provides [1, 4096]

    agg = MixVPR(
        in_channels=1,
        in_h=20,
        in_w=20,
        out_channels=4096,
        mix_depth=4,
        mlp_ratio=1,
        out_rows=1)
        # in_channels=1024,
        # in_h=20,
        # in_w=20,
        # out_channels=1024,
        # mix_depth=4,
        # mlp_ratio=1,
        # out_rows=4)

    model = seqNet_mix(inDims=4096, outDims=4096, seqL=10)

    # output = agg(x)
    # print(output.shape)
    output = model(x)
    print(output.shape)

"""
SEQNET
Input [24, 10, 4096]
- 24 is the batch size, indicating there are 24 sequences being processed in parallel.
- 10 is the sequence length, meaning each sequence contains 10 images. (10 of [1, 4096])
- 4096 represents the feature dimension of each element within the sequences. Each element is a descriptor or a feature vector, derived from an image, with a dimensionality of 4096.

MIXVPR
Input [1, 1024, 20, 20] for MixVPR indicates:
- 1: Batch size of one, meaning a single instance is processed at a time.
- 1024: dimensions, representing high-level features extracted from the data.
- 20 x 20: Spatial dimensions of each feature map, maintaining the spatial structure of the features.
"""

if __name__ == '__main__':
    main()