import time
import logging

import torch
import numpy as np
from PIL import Image
from bigotis.models import TamingDecoder
from PIL import Image

# import webserver

device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
print("DEVICE: ", device)

taming_decoder = TamingDecoder()
model = taming_decoder.to(device)


class LayerLoss:
    def __init__(
        self,
        layer,
    ):
        self.text_emb = taming_decoder.get_clip_text_encodings(layer.prompt, )
        self.text_emb = self.text_emb.detach()
        self.text_emb = self.text_emb.to(device)

        # get alpha mask
        mask = torch.from_numpy(layer.img[:, :, -1]).to(device)
        mask[mask > 0] = 1
        mask = mask.float()
        self.mask = mask

    def __call__(
        self,
        image,
    ):
        N, C, H, W = image.shape
        mask = torch.nn.functional.interpolate(
            self.mask[None, None],
            (H, W),
            mode="bilinear",
        )
        # merged = image * mask  #+ image.detach() * (1-mask)
        merged = image
        cutouts = taming_decoder.augment(merged, )
        image_emb = taming_decoder.get_clip_img_encodings(cutouts, )
        image_emb = image_emb.to(device)

        # dist = image_emb.sub(
        #     self.text_emb, ).norm(dim=2).div(2).arcsin().pow(2).mul(2)

        dist = -10 * torch.cosine_similarity(self.text_emb, image_emb)

        return dist.mean()


class LayeredGenerator(torch.nn.Module):
    def __init__(
        self,
        layer_list,
        target_img_size=128,
        lr: float = 0.5,
    ):
        super(LayeredGenerator, self).__init__()

        self.lr = lr
        self.target_img_size = target_img_size

        self.layer_loss_list = None
        self.reset_layers(layer_list, )

        self.gen_latent = None
        self.reset_gen_latent()

        self.optimizer = None
        self.reset_optimizer()

    def reset_layers(
        self,
        layer_list,
    ):
        self.layer_loss_list = [LayerLoss(layer) for layer in layer_list]

    def reset_gen_latent(self, ):
        self.gen_latent = taming_decoder.get_random_latent(
            target_img_height=self.target_img_size,
            target_img_width=self.target_img_size,
        )
        self.gen_latent = self.gen_latent.to(device)
        self.gen_latent.requires_grad = True
        self.gen_latent = torch.nn.Parameter(self.gen_latent)

    def reset_optimizer(self, ):
        self.optimizer = torch.optim.AdamW(
            params=[self.gen_latent],
            lr=self.lr,
            betas=(0.9, 0.999),
            weight_decay=0.1,
        )

    def optimize(self, ):
        try:
            x_rec = taming_decoder.get_img_rec_from_z(self.gen_latent)

            loss = 0
            loss_dict = {}
            for layer_idx, layer_loss in enumerate(self.layer_loss_list):
                logging.info(f"COMPUTING LOSS OF LAYER {layer_idx}")

                def scale_grad(grad):
                    N, C, H, W = grad.shape
                    mask = layer_loss.mask.clone()
                    for covering in self.layer_loss_list[layer_idx + 1:]:
                        mask -= covering.mask
                        mask.clamp_(0, 1)

                    return grad * torch.nn.functional.interpolate(
                        mask[None, None],
                        (H, W),
                    )

                # hook = self.gen_latent.register_hook(scale_grad)

                loss = layer_loss(x_rec, )

                self.optimizer.zero_grad()
                loss.backward(retain_graph=False, )
                self.optimizer.step()
                # hook.remove()

                logging.info(f"LOSS {loss}")
                loss_dict[f"layer_{layer_idx}"] = loss

        except Exception as e:
            logging.info(f"XXX: ERROR IN GENERATE {e}")

        return x_rec, loss_dict, None


if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class Layer:
        color: str
        strength: int
        prompt: str
        img: np.ndarray

    layer = Layer(
        color=000,
        strength=1,
        prompt="a pink dog",
        img=np.zeros((100, 100, 3)),
    )
    layer_list = [layer]

    layered_generator = LayeredGenerator(layer_list, )
    out = layered_generator()
